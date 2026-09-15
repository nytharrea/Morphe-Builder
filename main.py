import asyncio
import shutil
import subprocess
from pathlib import Path

from core import log
from core.apk.patcher import patch_apk
from core.apk.verify import verify_apk_signature
from core.apk.versions import extract_youtube_versions
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
from morphe_builder.hashes import sha256_file
from morphe_builder.manifest import write_build_manifest
from morphe_builder.services.rate_limit import jitter
from morphe_builder.version_policy import pick_version

DIST_DIR = Path.cwd() / "dist"


async def process_app(
    app_key: str,
    desktop_asset: dict,
    patch_assets: list[dict],
) -> dict | None:
    config = APPS_CONFIG[app_key]
    desktop = desktop_asset["name"]
    patches = [asset["name"] for asset in patch_assets]
    patch_sources = patch_sources_for(app_key)

    log.header(f"PROCESSING: {config['name'].upper()}")

    is_apkmirror_app = config["name"] in APKMIRROR_APPS

    selected_version = config.get("force_version")

    if not selected_version:
        try:
            patch_flags = []
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
            versions = extract_youtube_versions(output)
            if versions:
                selected_version = pick_version(versions, settings.version_policy)
        except Exception as e:
            log.warn(f"Could not fetch version list: {e}")

    if not selected_version:
        if not is_apkmirror_app:
            selected_version = "latest"
        else:
            latest = await apkmirror.get_latest_listing(config["name"])
            if latest and latest.get("version"):
                selected_version = latest["version"]

    if not selected_version:
        raise RuntimeError("Could not determine a suitable version number.")

    if is_apkmirror_app:
        apk_path = await apkmirror.download_apk(selected_version, config["name"], config.get("force_build"))
    else:
        apk_path = await github_apk.download_apk(selected_version, config["name"], config.get("force_build"))

    source_signatures = verify_apk_signature(apk_path, config["name"])

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
        "app_key": app_key,
        "app_name": config["name"],
        "pkg": config["pkg"],
        "display_name": display_name,
        "icon": config["icon"],
        "patch_source": config["patch_source"],
        "patch_sources": patch_sources,
        "name": final_name,
        "path": str(final_path),
        "version": selected_version,
        "sha256": sha256_file(final_path),
        "desktop": desktop_asset,
        "patches": patch_assets,
        "source_signatures": source_signatures,
    }


async def main():
    try:
        desktop_obj = await download_latest_github_asset(
            owner="MorpheApp",
            repo="morphe-desktop",
            prerelease=True,
            match=lambda n: "desktop" in n and n.endswith(".jar"),
        )

        target_app = settings.target_app
        apps_to_process = PROCESS_ORDER if target_app == "all" else [target_app]

        patches_pool: dict[str, dict | None] = {k: None for k in PATCH_SOURCES}

        for key, (owner, repo, _label) in PATCH_SOURCES.items():
            needed = any(key in patch_sources_for(k) for k in apps_to_process)
            if needed:
                patches_pool[key] = await download_latest_github_asset(
                    owner=owner,
                    repo=repo,
                    prerelease=True,
                    match=lambda n: n.endswith(".mpp"),
                )

        patched_apks_list = []
        failed_apps = []

        for app_key in apps_to_process:
            try:
                patch_assets = []
                for source in patch_sources_for(app_key):
                    patch_asset = patches_pool[source]
                    if patch_asset is None:
                        raise RuntimeError(f"No patch file resolved for source '{source}'")
                    patch_assets.append(patch_asset)
                result = await process_app(app_key, desktop_obj, patch_assets)
                if result:
                    patched_apks_list.append(result)
                    log.success(f"{app_key.upper()} done: {result['name']}")
                else:
                    failed_apps.append(app_key)
            except Exception as err:
                log.error(f"{app_key.upper()} failed, skipping: {err}")
                failed_apps.append(app_key)

            if APPS_CONFIG[app_key]["name"] in APKMIRROR_APPS and app_key != apps_to_process[-1]:
                delay = jitter(10.0, 4.0)
                log.wait(f"Waiting {delay:.0f}s before the next app (to reduce APKMirror request rate)...")
                await asyncio.sleep(delay)

        if patched_apks_list:
            names = ", ".join(apk["name"] for apk in patched_apks_list)
            log.saved(f"Patched APK(s) ready in {DIST_DIR}: {names}")
            manifest_file = write_build_manifest(
                DIST_DIR,
                patched_apks_list,
                tools={
                    "desktop": desktop_obj,
                    "patch_assets": {key: value for key, value in patches_pool.items() if value},
                },
                run_key=settings.target_app,
            )
            log.saved(f"Build manifest written: {manifest_file}")
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
        await apkmirror.close_browser()


if __name__ == "__main__":
    asyncio.run(main())
