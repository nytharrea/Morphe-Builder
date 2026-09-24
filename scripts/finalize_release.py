"""Entry point for the `finalize` job: matches the `patch` job's uploaded
.apk artifacts back to their build via catalog/apps.yaml's naming rules,
then publishes (or updates) the one GitHub Release. Run as
`python scripts/finalize_release.py` from the repo root."""

import asyncio
import json
from pathlib import Path

from morphe_builder import catalog, log, notify
from morphe_builder.fetchers.release_assets import download_latest_release_asset
from morphe_builder.release import (
    create_new_release,
    delete_other_releases,
    upload_microg_once,
    upload_patched_apks,
    upload_pothelper_once,
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
    """Reads back the dist/status-<build_key>.json files scripts/patch.py
    writes for a build it couldn't finish - uploaded in the exact same
    apk-${matrix.app} artifact as a successful build's .apk would be, so
    they show up right here alongside it with no separate download step.
    A build with no status file (the matrix job itself crashed before
    ever reaching patch.py's own try/except, say) just has no reason."""
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


async def main():
    if not settings.release_tag or not settings.release_name:
        raise RuntimeError("Missing RELEASE_TAG/RELEASE_NAME (expected to be set by prepare_release.py's output)")
    release_tag = settings.release_tag
    release_name = settings.release_name
    artifacts_dir = settings.artifacts_dir

    log.step(f"Scanning {artifacts_dir} for patched APKs...")
    matched, unmatched = find_patched_apks(artifacts_dir)

    for name in unmatched:
        log.warn(f"Could not match asset to a known app: {name}")

    log.info(f"Matched {len(matched)} app asset(s).")

    succeeded_keys = {apk["build_key"] for apk in matched}
    failed_keys = [key for key in catalog.BUILDS if key not in succeeded_keys]
    failure_reasons = find_failure_reasons(artifacts_dir)

    if not matched:
        log.error("No apps patched successfully in this run, skipping release creation.")
        await notify.notify(notify.format_all_failed(release_name, failed_keys, failure_reasons))
        return

    body = "### Latest Patched APKs\n\n"
    for apk in matched:
        icon = catalog.BUILDS[apk["build_key"]]["icon"]
        body += f'* <img src="{icon}" width="16" height="16"> **{apk["display_name"]}** - `{apk["version"]}`\n'

    body += "\n---\n\n"

    used_sources: set[str] = set()
    for apk in matched:
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
            return (
                f"\n<details>\n<summary>{label} Release Notes ({asset['tag']})</summary>\n<br>\n\n"
                f"{asset['body']}\n\n</details>\n"
            )
        except Exception as e:
            log.warn(f"Could not fetch release notes for {label}: {e}")
            return ""

    source_keys = [key for key in sorted(used_sources) if key in catalog.PATCH_SOURCES]
    body += "".join(await asyncio.gather(*(_fetch_release_notes(key) for key in source_keys)))

    log.step(f"Creating release: {release_tag}")
    release = await create_new_release(release_tag, release_name, body, draft=False)
    log.success(f"Release created: {release['tag_name']} (id={release['id']})")

    log.step(f"Uploading {len(matched)} patched APK(s) (up to {settings.upload_concurrency} at once)...")
    await upload_patched_apks(release, [apk["path"] for apk in matched])

    if any(apk["build_key"] in ("youtube", "youtube-music") for apk in matched):
        await asyncio.gather(upload_microg_once(release), upload_pothelper_once(release))

    log.success("All apps successfully published under one release!")

    try:
        await delete_other_releases(release["id"])
        log.info("Old releases deleted.")
    except Exception as e:
        log.warn(f"Failed to delete old releases: {e}")

    release_url = release.get("html_url") or (
        f"https://github.com/{settings.github_repository}/releases/tag/{release_tag}"
    )
    await notify.notify(notify.format_summary(release_name, release_url, matched, failed_keys, failure_reasons))


if __name__ == "__main__":
    asyncio.run(main())
