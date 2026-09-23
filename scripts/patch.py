"""Entry point for the `patch` job (one matrix runner = one build key, via
TARGET_APP). Run as `python scripts/patch.py` from the repo root, with the
package installed (`pip install -e .`)."""

import asyncio
import random
import shutil
import subprocess
from pathlib import Path

from morphe_builder import catalog, log
from morphe_builder.apk.patcher import patch_apk
from morphe_builder.apk.verify import verify_apk_signature
from morphe_builder.apk.versions import extract_cli_versions, pick_latest_version
from morphe_builder.fetchers import apkmirror, github_app
from morphe_builder.fetchers.release_assets import download_latest_release_asset
from morphe_builder.settings import settings

DIST_DIR = Path.cwd() / "dist"


async def process_build(build_key: str, desktop: str, patches: list[str]) -> dict | None:
    build = catalog.BUILDS[build_key]
    app_slug = build["app_slug"]
    source = build["apk_source"]
    is_apkmirror_app = source["type"] == "apkmirror"

    log.header(f"PROCESSING: {app_slug.upper()}")

    selected_version = build.get("force_version")

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
                    build["pkg"],
                    *patch_flags,
                    "--include-experimental",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                stderr_tail = (result.stderr or "").strip().splitlines()[-5:]
                log.warn(
                    f"'list-versions' exited with code {result.returncode}, "
                    f"skipping CLI-reported versions: {' | '.join(stderr_tail) or '(no output)'}"
                )
            else:
                versions = extract_cli_versions(result.stdout or "")
                if versions:
                    selected_version = pick_latest_version(versions)
        except Exception as e:
            log.warn(f"Could not fetch version list: {e}")

    if not selected_version:
        if not is_apkmirror_app:
            selected_version = "latest"
        else:
            latest = await apkmirror.get_latest_listing(app_slug, source)
            if latest and latest.get("version"):
                selected_version = latest["version"]

    if not selected_version:
        raise RuntimeError("Could not determine a suitable version number.")

    if is_apkmirror_app:
        apk_path = await apkmirror.download_apk(selected_version, app_slug, source, build.get("force_build"))
    else:
        apk_path = await github_app.download_apk(selected_version, app_slug, source, build.get("force_build"))

    verify_apk_signature(apk_path, app_slug)

    patched_apk = patch_apk(
        desktop,
        patches,
        apk_path,
        exclude=build.get("exclude"),
        enable=build.get("enable"),
        arch=build["arch"],
    )

    if not Path(patched_apk).exists():
        return None

    display_name, source_tag = catalog.get_release_naming(build_key)
    if source_tag:
        final_name = f"{display_name}-{selected_version}-{source_tag}.apk"
    else:
        final_name = f"{display_name}-{selected_version}.apk"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    final_path = DIST_DIR / final_name

    shutil.copyfile(patched_apk, final_path)

    return {
        "app_name": app_slug,
        "display_name": display_name,
        "icon": build["icon"],
        "patch_source": build["patch_sources"],
        "name": final_name,
        "path": str(final_path),
        "version": selected_version,
    }


async def main():
    try:
        desktop_obj = await download_latest_release_asset(
            owner="MorpheApp",
            repo="morphe-desktop",
            prerelease=True,
            match=lambda n: "desktop" in n and n.endswith(".jar"),
        )
        desktop = desktop_obj["name"]

        target_app = settings.target_app
        builds_to_process = list(catalog.BUILDS) if target_app == "all" else [target_app]

        patches_pool: dict[str, str | None] = dict.fromkeys(catalog.PATCH_SOURCES)

        for key, source in catalog.PATCH_SOURCES.items():
            needed = any(key in catalog.patch_sources_for(k) for k in builds_to_process)
            if needed:
                asset = await download_latest_release_asset(
                    owner=source["owner"],
                    repo=source["repo"],
                    prerelease=True,
                    match=lambda n: n.endswith(".mpp"),
                )
                patches_pool[key] = asset["name"]

        patched_apks_list = []
        failed_builds = []

        for build_key in builds_to_process:
            try:
                patch_files = []
                for source_key in catalog.patch_sources_for(build_key):
                    patch_file = patches_pool[source_key]
                    if patch_file is None:
                        raise RuntimeError(f"No patch file resolved for source '{source_key}'")
                    patch_files.append(patch_file)
                result = await process_build(build_key, desktop, patch_files)
                if result:
                    patched_apks_list.append(result)
                    log.success(f"{build_key.upper()} done: {result['name']}")
                else:
                    failed_builds.append(build_key)
            except Exception as err:
                log.error(f"{build_key.upper()} failed, skipping: {err}")
                failed_builds.append(build_key)

            is_last = build_key == builds_to_process[-1]
            if catalog.BUILDS[build_key]["apk_source"]["type"] == "apkmirror" and not is_last:
                delay = random.uniform(6.0, 14.0)
                log.wait(f"Waiting {delay:.0f}s before the next app (to reduce APKMirror request rate)...")
                await asyncio.sleep(delay)

        if patched_apks_list:
            names = ", ".join(apk["name"] for apk in patched_apks_list)
            log.saved(f"Patched APK(s) ready in {DIST_DIR}: {names}")
            log.info("These will be picked up as a workflow artifact and published in the finalize job.")

        if failed_builds:
            log.error(f"Failed build(s): {', '.join(failed_builds)}")
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
