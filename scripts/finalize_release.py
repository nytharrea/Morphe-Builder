"""Entry point for the `finalize` job.

Publishes a full release *snapshot*:
- New APKs produced by this run's matrix jobs
- Unchanged APKs copied from the previous release (SKIP decisions)
- build-manifest.json describing every build's version/patch/config state

Run as `python scripts/finalize_release.py` from the repo root.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from morphe_builder import catalog, log, notify
from morphe_builder.build.models import BuildPlan, BuildRecord, Manifest
from morphe_builder.build.state import MANIFEST_ASSET_NAME, download_previous_asset, write_manifest
from morphe_builder.fetchers.release_assets import download_latest_release_asset
from morphe_builder.release import (
    create_new_release,
    delete_other_releases,
    upload_microg_once,
    upload_patched_apks,
    upload_pothelper_once,
    upload_with_replace,
)
from morphe_builder.settings import settings


def _build_asset_candidates() -> list[tuple[str, str, str | None]]:
    candidates = []
    for build_key in catalog.BUILDS:
        display_name, tag = catalog.get_release_naming(build_key)
        candidates.append((build_key, display_name, tag))
    candidates.sort(key=lambda c: -len(c[1]))
    return candidates


_ASSET_CANDIDATES = _build_asset_candidates()


def match_asset(file_name: str):
    if not file_name.lower().endswith(".apk"):
        return None
    if file_name.lower().startswith("microg"):
        return None
    if file_name.lower().startswith("pothelper"):
        return None

    base = file_name[:-4]

    for build_key, display_name, tag in _ASSET_CANDIDATES:
        prefix = display_name + "-"
        if not base.lower().startswith(prefix.lower()):
            continue

        remainder = base[len(prefix) :]

        if tag:
            suffix = f"-{tag}"
            if not remainder.lower().endswith(suffix.lower()):
                continue
            remainder = remainder[: -len(suffix)]

        return build_key, display_name, remainder

    return None


def find_patched_apks(artifacts_dir: Path):
    matched = []
    unmatched = []

    for apk_path in sorted(artifacts_dir.rglob("*.apk")):
        result = match_asset(apk_path.name)
        if result:
            build_key, display_name, version = result
            matched.append(
                {
                    "build_key": build_key,
                    "display_name": display_name,
                    "version": version,
                    "path": str(apk_path),
                    "name": apk_path.name,
                }
            )
        else:
            unmatched.append(apk_path.name)

    return matched, unmatched


def find_failure_reasons(artifacts_dir: Path) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for status_path in sorted(artifacts_dir.rglob("status-*.json")):
        try:
            data = json.loads(status_path.read_text())
            build_key = data.get("build_key")
            if build_key:
                reasons[build_key] = str(data.get("error") or "unknown error")
        except (json.JSONDecodeError, OSError) as e:
            log.warn(f"Could not read failure status {status_path}: {e}")
    return reasons


def _neutralize_github_mentions(text: str) -> str:
    return re.sub(r"@([A-Za-z0-9_-]+)", r"\1", text)


def _load_plan() -> BuildPlan | None:
    """plan.json is produced by prepare and uploaded as a workflow artifact."""
    candidates = [
        Path.cwd() / "plan.json",
        Path.cwd() / "plan" / "plan.json",
        settings.artifacts_dir / "plan.json",
    ]
    # Also search under artifacts/
    if settings.artifacts_dir.exists():
        candidates.extend(settings.artifacts_dir.rglob("plan.json"))

    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                plan = BuildPlan.from_dict(data)
                log.info(f"Loaded plan from {path} ({len(plan.decisions)} decision(s))")
                return plan
            except Exception as e:
                log.warn(f"Could not parse plan at {path}: {e}")
    log.warn("No plan.json found — finalize will only publish newly built APKs")
    return None


async def _carry_over_skips(plan: BuildPlan, carry_dir: Path) -> list[dict]:
    """Download unchanged APKs from the previous release into carry_dir."""
    carried: list[dict] = []
    if not plan.previous_release_id:
        log.info("No previous release id; cannot carry over skipped APKs")
        return carried

    carry_dir.mkdir(parents=True, exist_ok=True)

    for d in plan.decisions:
        if d.decision != "skip":
            continue
        if not d.previous_apk_name:
            log.warn(f"Skip decision for {d.build_key} has no previous_apk_name; cannot carry over")
            continue

        dest = carry_dir / d.previous_apk_name
        result = await download_previous_asset(plan.previous_release_id, d.previous_apk_name, dest)
        if result and result.exists():
            carried.append(
                {
                    "build_key": d.build_key,
                    "display_name": d.display_name,
                    "version": d.app_version,
                    "path": str(result),
                    "name": result.name,
                    "carried": True,
                }
            )
            log.success(f"Carried over {d.build_key}: {result.name}")
        else:
            log.warn(f"Failed to carry over {d.build_key} ({d.previous_apk_name}); it will be missing from this release")

    return carried


def _build_manifest(plan: BuildPlan | None, all_apks: list[dict]) -> Manifest:
    """Assemble the new build-manifest from plan decisions + actual APK names."""
    by_key = {a["build_key"]: a for a in all_apks}
    builds: dict[str, BuildRecord] = {}

    if plan:
        for d in plan.decisions:
            apk = by_key.get(d.build_key)
            apk_name = apk["name"] if apk else (d.previous_apk_name or f"{d.display_name}-{d.app_version}.apk")
            builds[d.build_key] = BuildRecord(
                build_key=d.build_key,
                app_version=d.app_version,
                patches=dict(d.patches),
                config_hash=d.config_hash,
                apk_name=apk_name,
                display_name=d.display_name,
            )
    else:
        # Fallback without plan: only record what we actually have
        for apk in all_apks:
            builds[apk["build_key"]] = BuildRecord(
                build_key=apk["build_key"],
                app_version=str(apk.get("version") or "unknown"),
                patches={},
                config_hash="",
                apk_name=apk["name"],
                display_name=apk.get("display_name") or apk["build_key"],
            )

    return Manifest(
        schema_version=1,
        release_tag=settings.release_tag or "",
        builds=builds,
    )


async def main():
    if not settings.release_tag or not settings.release_name:
        raise RuntimeError("Missing RELEASE_TAG/RELEASE_NAME (expected to be set by prepare_release.py's output)")
    release_tag = settings.release_tag
    release_name = settings.release_name
    artifacts_dir = settings.artifacts_dir

    plan = _load_plan()

    log.step(f"Scanning {artifacts_dir} for patched APKs...")
    matched, unmatched = find_patched_apks(artifacts_dir)

    for name in unmatched:
        log.warn(f"Unmatched artifact (ignored): {name}")

    failure_reasons = find_failure_reasons(artifacts_dir)
    failed_keys = sorted(failure_reasons.keys())

    # Carry over skipped APKs from previous release
    carried: list[dict] = []
    if plan and plan.to_skip:
        log.step(f"Carrying over {len(plan.to_skip)} unchanged APK(s) from previous release...")
        carried = await _carry_over_skips(plan, Path.cwd() / "carried")

    all_apks = matched + carried
    log.info(f"Total APKs for this snapshot: {len(all_apks)} (new={len(matched)}, carried={len(carried)})")

    # Release body
    body_lines = [
        f"**Snapshot** — {len(all_apks)} app(s)",
        "",
    ]
    if plan:
        body_lines.append(f"- Built this run: {len(plan.to_build)}")
        body_lines.append(f"- Carried from previous: {len(plan.to_skip)}")
        body_lines.append("")

    for apk in sorted(all_apks, key=lambda a: a["display_name"].lower()):
        flag = " (carried)" if apk.get("carried") else ""
        body_lines.append(f"- **{apk['display_name']}** `{apk['version']}`{flag}")

    body = "\n".join(body_lines) + "\n"

    used_sources: set[str] = set()
    for apk in all_apks:
        used_sources.update(catalog.patch_sources_for(apk["build_key"]))

    async def _fetch_release_notes(key: str) -> str:
        source = catalog.PATCH_SOURCES[key]
        label = source["label"]
        try:
            asset = await download_latest_release_asset(
                owner=source["owner"],
                repo=source["repo"],
                prerelease=True,
                match=lambda n: n.endswith(".mpp"),
            )
            notes = _neutralize_github_mentions(asset["body"] or "")
            open_tag = chr(60) + "details" + chr(62) + "\n"
            open_tag += chr(60) + "summary" + chr(62)
            open_tag += "{} Release Notes ({})".format(label, asset["tag"])
            open_tag += chr(60) + "/summary" + chr(62) + "\n" + chr(60) + "br" + chr(62) + "\n\n"
            close_tag = "\n\n" + chr(60) + "/details" + chr(62) + "\n"
            return "\n" + open_tag + notes + close_tag
        except Exception as e:
            log.warn(f"Could not fetch release notes for {label}: {e}")
            return ""

    source_keys = [key for key in sorted(used_sources) if key in catalog.PATCH_SOURCES]
    body += "".join(await asyncio.gather(*(_fetch_release_notes(key) for key in source_keys)))
    body = _neutralize_github_mentions(body)

    log.step(f"Creating release: {release_tag}")
    release = await create_new_release(release_tag, release_name, body, draft=False)
    log.success(f"Release created: {release['tag_name']} (id={release['id']})")

    if all_apks:
        log.step(f"Uploading {len(all_apks)} APK(s) (up to {settings.upload_concurrency} at once)...")
        await upload_patched_apks(release, [apk["path"] for apk in all_apks])

    # Always publish MicroG / PotHelper when youtube builds are present in the snapshot
    youtube_keys = {"youtube", "youtube-music"}
    if any(apk["build_key"] in youtube_keys for apk in all_apks):
        await asyncio.gather(upload_microg_once(release), upload_pothelper_once(release))

    # Write and upload build-manifest.json
    manifest = _build_manifest(plan, all_apks)
    manifest_path = Path.cwd() / MANIFEST_ASSET_NAME
    write_manifest(manifest_path, manifest)
    log.step("Uploading build-manifest.json...")
    await upload_with_replace(release, str(manifest_path))

    log.success("Release snapshot published!")

    try:
        await delete_other_releases(release["id"])
        log.info("Old releases deleted.")
    except Exception as e:
        log.warn(f"Failed to delete old releases: {e}")

    release_url = release.get("html_url") or (
        f"https://github.com/{settings.github_repository}/releases/tag/{release_tag}"
    )
    await notify.notify(notify.format_summary(release_name, release_url, all_apks, failed_keys, failure_reasons))


if __name__ == "__main__":
    asyncio.run(main())
