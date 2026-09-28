"""Decide BUILD vs SKIP for every catalog build.

Resolution order for the current app version (same policy as scripts/patch.py):

1. force_version on the build config
2. morphe-desktop CLI ``list-versions`` (patch-recommended)
3. APKMirror / GitHub latest listing
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from morphe_builder import catalog, log
from morphe_builder.apk.versions import extract_cli_versions, pick_latest_version
from morphe_builder.fetchers import apkmirror, github_app
from morphe_builder.fetchers.release_assets import download_latest_release_asset
from morphe_builder.settings import settings

from .fingerprint import config_fingerprint
from .models import BuildDecision, BuildPlan, Manifest
from .state import load_previous_manifest


async def _resolve_patch_tags() -> dict[str, str]:
    """Map every catalog patch_source key -> latest release tag (e.g. v1.5)."""
    tags: dict[str, str] = {}
    for key, source in catalog.PATCH_SOURCES.items():
        try:
            asset = await download_latest_release_asset(
                owner=source["owner"],
                repo=source["repo"],
                prerelease=True,
                match=lambda n: n.endswith(".mpp"),
            )
            tags[key] = asset.get("tag") or "unknown"
            log.info(f"Patch source '{key}': {tags[key]}")
        except Exception as e:
            log.warn(f"Could not resolve patch tag for '{key}': {e}")
            tags[key] = "unknown"
    return tags


async def _resolve_app_version(build_key: str, desktop_jar: str, patch_files: list[str]) -> str:
    """Mirror scripts/patch.py version selection without downloading the APK."""
    build = catalog.BUILDS[build_key]
    app_slug = build["app_slug"]
    source = build["apk_source"]

    selected = build.get("force_version")
    if selected:
        return selected

    try:
        patch_flags: list[str] = []
        for p in patch_files:
            patch_flags += ["--patches", p]

        result = subprocess.run(
            [
                "java",
                "-jar",
                desktop_jar,
                "list-versions",
                "-f",
                build["pkg"],
                *patch_flags,
                "--include-experimental",
            ],
            capture_output=True,
            text=True,
            timeout=settings.download_timeout,
        )
        if result.returncode == 0:
            versions = extract_cli_versions(result.stdout or "", app_slug)
            if versions:
                picked = pick_latest_version(versions)
                if picked:
                    return picked
        else:
            log.warn(
                f"list-versions for {build_key} exited {result.returncode}; "
                "falling through to source latest"
            )
    except Exception as e:
        log.warn(f"CLI version resolve failed for {build_key}: {e}")

    if source["type"] == "apkmirror":
        latest = await apkmirror.get_latest_listing(app_slug, source)
        if latest and latest.get("version"):
            return str(latest["version"])
    elif source["type"] == "github":
        try:
            listing = await github_app.get_latest_listing(app_slug, source)
            if listing and listing.get("version"):
                return str(listing["version"])
        except Exception as e:
            log.warn(f"GitHub latest listing failed for {build_key}: {e}")
        return "latest"

    raise RuntimeError(f"Could not determine version for {build_key}")


def _patches_for_build(build_key: str, all_tags: dict[str, str]) -> dict[str, str]:
    return {src: all_tags.get(src, "unknown") for src in catalog.patch_sources_for(build_key)}


def _decide(
    build_key: str,
    *,
    app_version: str,
    patches: dict[str, str],
    cfg_hash: str,
    previous: Manifest | None,
    force_rebuild: bool,
) -> BuildDecision:
    build = catalog.BUILDS[build_key]
    display_name, _ = catalog.get_release_naming(build_key)
    display_name = display_name or build["display_name"]

    prev_rec = previous.builds.get(build_key) if previous else None

    if force_rebuild:
        return BuildDecision(
            build_key=build_key,
            decision="build",
            reason="force_rebuild",
            app_version=app_version,
            previous_app_version=prev_rec.app_version if prev_rec else None,
            patches=patches,
            previous_patches=prev_rec.patches if prev_rec else None,
            config_hash=cfg_hash,
            previous_config_hash=prev_rec.config_hash if prev_rec else None,
            previous_apk_name=prev_rec.apk_name if prev_rec else None,
            display_name=display_name,
        )

    if prev_rec is None:
        return BuildDecision(
            build_key=build_key,
            decision="build",
            reason="no previous record",
            app_version=app_version,
            previous_app_version=None,
            patches=patches,
            previous_patches=None,
            config_hash=cfg_hash,
            previous_config_hash=None,
            previous_apk_name=None,
            display_name=display_name,
        )

    reasons: list[str] = []
    if prev_rec.app_version != app_version:
        reasons.append(f"app {prev_rec.app_version} → {app_version}")
    if prev_rec.patches != patches:
        reasons.append("patch update")
    if prev_rec.config_hash != cfg_hash:
        reasons.append("config changed")

    if reasons:
        return BuildDecision(
            build_key=build_key,
            decision="build",
            reason="; ".join(reasons),
            app_version=app_version,
            previous_app_version=prev_rec.app_version,
            patches=patches,
            previous_patches=prev_rec.patches,
            config_hash=cfg_hash,
            previous_config_hash=prev_rec.config_hash,
            previous_apk_name=prev_rec.apk_name,
            display_name=display_name,
        )

    return BuildDecision(
        build_key=build_key,
        decision="skip",
        reason="unchanged",
        app_version=app_version,
        previous_app_version=prev_rec.app_version,
        patches=patches,
        previous_patches=prev_rec.patches,
        config_hash=cfg_hash,
        previous_config_hash=prev_rec.config_hash,
        previous_apk_name=prev_rec.apk_name,
        display_name=display_name,
    )


def _log_decision(d: BuildDecision) -> None:
    if d.decision == "skip":
        log.info(
            f"⏭️  {d.build_key}: SKIP — app {d.app_version}, patches {d.patches}, config unchanged"
        )
    else:
        log.step(f"🔨 {d.build_key}: BUILD — {d.reason}")
        if d.previous_app_version and d.previous_app_version != d.app_version:
            log.info(f"  App: {d.previous_app_version} → {d.app_version}")
        else:
            log.info(f"  App: {d.app_version}")
        log.info(f"  Patches: {d.patches}")
        if d.previous_config_hash and d.previous_config_hash != d.config_hash:
            log.info("  Config: changed")


async def create_plan(
    *,
    release_tag: str,
    release_name: str,
    force_rebuild: bool = False,
    target_app: str = "all",
) -> BuildPlan:
    """Full prepare-time plan: resolve versions, compare to previous manifest."""
    log.header("BUILD PLANNER")

    # Ensure desktop jar is present (download_shared_assets should have run,
    # but be resilient).
    desktop_obj = await download_latest_release_asset(
        owner="MorpheApp",
        repo="morphe-desktop",
        prerelease=True,
        match=lambda n: "desktop" in n and n.endswith(".jar"),
    )
    desktop_jar = desktop_obj["name"]
    if not Path(desktop_jar).exists():
        raise RuntimeError(f"Desktop jar not found on disk: {desktop_jar}")

    previous, prev_tag, prev_id = await load_previous_manifest()

    log.step("Resolving current patch source tags...")
    all_patch_tags = await _resolve_patch_tags()

    # Pre-resolve .mpp file names for CLI list-versions (already cached on disk).
    patches_pool: dict[str, str] = {}
    for key, source in catalog.PATCH_SOURCES.items():
        asset = await download_latest_release_asset(
            owner=source["owner"],
            repo=source["repo"],
            prerelease=True,
            match=lambda n: n.endswith(".mpp"),
        )
        patches_pool[key] = asset["name"]

    build_keys = list(catalog.BUILDS) if target_app == "all" else [target_app]
    if target_app != "all" and target_app not in catalog.BUILDS:
        raise RuntimeError(f"Unknown target_app: {target_app}")

    decisions: list[BuildDecision] = []

    for build_key in build_keys:
        build = catalog.BUILDS[build_key]
        patch_files = [patches_pool[s] for s in build["patch_sources"]]
        try:
            app_version = await _resolve_app_version(build_key, desktop_jar, patch_files)
        except Exception as e:
            log.warn(f"Version resolve failed for {build_key}: {e}; forcing BUILD")
            app_version = "unknown"
            # Force build when we cannot compare
            d = BuildDecision(
                build_key=build_key,
                decision="build",
                reason=f"version resolve failed ({e})",
                app_version=app_version,
                previous_app_version=None,
                patches=_patches_for_build(build_key, all_patch_tags),
                previous_patches=None,
                config_hash=config_fingerprint(build),
                previous_config_hash=None,
                previous_apk_name=None,
                display_name=build["display_name"],
            )
            _log_decision(d)
            decisions.append(d)
            continue

        patches = _patches_for_build(build_key, all_patch_tags)
        cfg_hash = config_fingerprint(build)
        d = _decide(
            build_key,
            app_version=app_version,
            patches=patches,
            cfg_hash=cfg_hash,
            previous=previous,
            force_rebuild=force_rebuild,
        )
        _log_decision(d)
        decisions.append(d)

    plan = BuildPlan(
        release_tag=release_tag,
        release_name=release_name,
        decisions=decisions,
        previous_release_tag=prev_tag,
        previous_release_id=prev_id,
        force_rebuild=force_rebuild,
    )

    log.header("PLAN SUMMARY")
    log.info(f"BUILD: {len(plan.to_build)}  |  SKIP: {len(plan.to_skip)}")
    if plan.to_build:
        log.info(f"  build → {', '.join(plan.to_build)}")
    if plan.to_skip:
        log.info(f"  skip  → {', '.join(plan.to_skip)}")

    return plan
