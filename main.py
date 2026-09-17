import asyncio
import random
import shutil
import subprocess
from pathlib import Path

from core import log
from core.apk.patcher import patch_apk
from core.apk.verify import normalize_download_path, verify_apk_signature
from core.apk.versions import extract_youtube_versions, rank_versions
from core.config import (
    APKMIRROR_APPS,
    APPS_CONFIG,
    PATCH_SOURCES,
    PROCESS_ORDER,
    get_release_naming,
    patch_sources_for,
)
from core.patch_tools import download_latest_github_asset
from core.settings import settings
from core.sources import apkmirror, github_apk

DIST_DIR = Path.cwd() / "dist"

# Failures that mean "this exact version is not on the mirror" — safe to try the next.
_VERSION_MISSING_MARKERS = (
    "No APKMirror release page found",
    "No matching variant found on APKMirror",
    "Giving up on ",
)


def _is_version_missing_error(err: BaseException) -> bool:
    msg = str(err)
    return any(marker in msg for marker in _VERSION_MISSING_MARKERS)


async def process_app(app_key: str, desktop: str, patches: list[str]) -> dict | None:
    config = APPS_CONFIG[app_key]
    log.header(f"PROCESSING: {config['name'].upper()}")

    is_apkmirror_app = config["name"] in APKMIRROR_APPS
    app_name = config["name"]

    # Candidate versions, best-first. force_version wins and is not retried.
    version_candidates: list[str] = []
    forced = config.get("force_version")
    if forced:
        version_candidates = [forced]
    else:
        try:
            patch_flags: list[str] = []
            for p in patches:
                patch_flags += ["--patches", p]

            result = subprocess.run(
                [
                    "java",
                    "-jar",
                    desktop,
                    "list-versions",
                    "-f",
                    config["pkg"],
                    *patch_flags,
                    "--include-experimental",
                ],
                capture_output=True,
                text=True,
            )
            output = (result.stdout or "") + (result.stderr or "")
            patcher_versions = extract_youtube_versions(output)
            version_candidates = rank_versions(patcher_versions)
            if version_candidates:
                log.info(
                    f"Patcher candidates ({len(version_candidates)}): "
                    + ", ".join(version_candidates[:8])
                    + ("…" if len(version_candidates) > 8 else "")
                )
        except Exception as e:
            log.warn(f"Could not fetch version list: {e}")

    if not version_candidates:
        if not is_apkmirror_app:
            version_candidates = ["latest"]
        else:
            latest = await apkmirror.get_latest_listing(app_name)
            if latest and latest.get("version"):
                version_candidates = [str(latest["version"])]
                log.notice(f"No patcher list – falling back to APKMirror latest: {version_candidates[0]}")

    if not version_candidates:
        raise RuntimeError("Could not determine a suitable version number.")

    selected_version: str | None = None
    apk_path: str | None = None
    last_error: Exception | None = None

    if is_apkmirror_app:
        for idx, candidate in enumerate(version_candidates):
            try:
                if idx > 0:
                    log.notice(
                        f"{candidate} – retrying next patcher-compatible version "
                        f"({idx + 1}/{len(version_candidates)}) after previous miss on APKMirror"
                    )
                apk_path = await apkmirror.download_apk(candidate, app_name, config.get("force_build"))
                selected_version = candidate
                if idx > 0:
                    log.success(
                        f"Using fallback version {candidate} (patcher preferred a newer build not on APKMirror)"
                    )
                break
            except Exception as err:
                last_error = err if isinstance(err, Exception) else Exception(str(err))
                if forced or not _is_version_missing_error(err):
                    raise
                log.warn(f"Version {candidate} not available on APKMirror: {err}")
                continue

        # Do NOT fall back to a random APKMirror "latest" outside the patcher
        # list — that yields "Applying 0 patches" (e.g. Termius 7.8.2 vs 7.9.0).

        if apk_path is None or selected_version is None:
            raise last_error or RuntimeError(
                f"No downloadable patcher-compatible version on APKMirror for {app_name} "
                f"(tried: {', '.join(version_candidates)})"
            )
    else:
        selected_version = version_candidates[0]
        apk_path = await github_apk.download_apk(selected_version, app_name, config.get("force_build"))

    verify_apk_signature(apk_path, config["name"])

    # Only fix the filename (.apk / .apkm). Never unpack the bundle — Morphe
    # needs the full APKM for apps with splits (Instagram, Brave, …).
    apk_path = normalize_download_path(apk_path)

    patched_apk = patch_apk(
        desktop,
        patches,
        apk_path,
        exclude=config.get("exclude"),
        enable=config.get("enable"),
        arch=config["arch"],
    )

    if not Path(patched_apk).exists():
        return None

    display_name, source_tag = get_release_naming(app_key)
    if source_tag:
        final_name = f"{display_name}-{selected_version}-{source_tag}.apk"
    else:
        final_name = f"{display_name}-{selected_version}.apk"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    final_path = DIST_DIR / final_name

    shutil.copyfile(patched_apk, final_path)

    return {
        "app_name": config["name"],
        "display_name": display_name,
        "icon": config["icon"],
        "patch_source": config["patch_source"],
        "name": final_name,
        "path": str(final_path),
        "version": selected_version,
    }


async def main():
    try:
        desktop_obj = await download_latest_github_asset(
            owner="MorpheApp",
            repo="morphe-desktop",
            prerelease=True,
            match=lambda n: "desktop" in n and n.endswith(".jar"),
        )
        desktop = desktop_obj["name"]

        target_app = settings.target_app
        apps_to_process = PROCESS_ORDER if target_app == "all" else [target_app]

        patches_pool: dict[str, str | None] = {k: None for k in PATCH_SOURCES}

        for key, (owner, repo, _label) in PATCH_SOURCES.items():
            needed = any(key in patch_sources_for(k) for k in apps_to_process)
            if needed:
                asset = await download_latest_github_asset(
                    owner=owner,
                    repo=repo,
                    prerelease=True,
                    match=lambda n: n.endswith(".mpp"),
                )
                patches_pool[key] = asset["name"]

        patched_apks_list = []
        failed_apps = []

        for app_key in apps_to_process:
            try:
                patch_files = []
                for source in patch_sources_for(app_key):
                    patch_file = patches_pool[source]
                    if patch_file is None:
                        raise RuntimeError(f"No patch file resolved for source '{source}'")
                    patch_files.append(patch_file)
                result = await process_app(app_key, desktop, patch_files)
                if result:
                    patched_apks_list.append(result)
                    log.success(f"{app_key.upper()} done: {result['name']}")
                else:
                    failed_apps.append(app_key)
            except Exception as err:
                log.error(f"{app_key.upper()} failed, skipping: {err}")
                failed_apps.append(app_key)

            if APPS_CONFIG[app_key]["name"] in APKMIRROR_APPS and app_key != apps_to_process[-1]:
                delay = random.uniform(6.0, 14.0)
                log.wait(f"Waiting {delay:.0f}s before the next app (to reduce APKMirror request rate)...")
                await asyncio.sleep(delay)

        if patched_apks_list:
            names = ", ".join(apk["name"] for apk in patched_apks_list)
            log.saved(f"Patched APK(s) ready in {DIST_DIR}: {names}")
            log.info("These will be picked up as a workflow artifact and published in the finalize job.")

        if failed_apps:
            log.error(f"Failed app(s): {', '.join(failed_apps)}")
            raise SystemExit(1)

    except SystemExit:
        raise
    except Exception as err:
        log.error(f"Fatal error: {err}")
        raise SystemExit(1) from err
    finally:
        await apkmirror.close_session()


if __name__ == "__main__":
    asyncio.run(main())
