"""Load / write the per-release build-manifest.json.

The manifest is uploaded as a release asset named ``build-manifest.json`` so
the next prepare job can download it and decide BUILD vs SKIP without
re-parsing release notes or APK filenames.
"""

from __future__ import annotations

import json
from pathlib import Path

from morphe_builder import log
from morphe_builder.fetchers.release_assets import download_latest_release_asset
from morphe_builder.http import github_headers, new_session
from morphe_builder.release import get_assets, get_latest_release  # noqa: F401 - get_latest_release used below
from morphe_builder.settings import settings

from .models import Manifest

MANIFEST_ASSET_NAME = "build-manifest.json"


async def load_previous_manifest() -> tuple[Manifest | None, str | None, int | None]:
    """Fetch build-manifest.json from the most recent non-draft release.

    Returns (manifest | None, previous_tag | None, previous_release_id | None).
    Missing manifest (first run, or old releases before this feature) is not
    an error — the planner will simply schedule every build.
    """
    if not settings.github_repository:
        log.warn("GITHUB_REPOSITORY unset; cannot load previous build-manifest")
        return None, None, None

    try:
        release = await get_latest_release()
    except Exception as e:
        log.warn(f"Could not fetch latest release for previous manifest: {e}")
        return None, None, None

    if not release:
        log.info("No previous release found; all builds will run")
        return None, None, None

    tag = release.get("tag_name") or ""
    release_id = release.get("id")
    log.info(f"Previous release: {tag} (id={release_id})")

    assets = release.get("assets") or []
    manifest_asset = next((a for a in assets if a.get("name") == MANIFEST_ASSET_NAME), None)

    if not manifest_asset:
        # Fallback: try downloading via the shared helper (same name match).
        try:
            asset = await download_latest_release_asset(
                owner=settings.github_repository.split("/")[0],
                repo=settings.github_repository.split("/")[1],
                prerelease=False,
                match=lambda n: n == MANIFEST_ASSET_NAME,
            )
            path = Path(asset["name"])
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                path.unlink(missing_ok=True)
                manifest = Manifest.from_dict(data)
                log.success(f"Loaded previous build-manifest ({len(manifest.builds)} build(s))")
                return manifest, tag, release_id
        except Exception as e:
            log.info(f"No build-manifest.json on previous release ({e}); all builds will run")
            return None, tag, release_id

        log.info("No build-manifest.json on previous release; all builds will run")
        return None, tag, release_id

    # Download the asset content via GitHub API
    url = manifest_asset.get("url") or manifest_asset.get("browser_download_url")
    if not url:
        log.warn("Manifest asset has no download URL")
        return None, tag, release_id

    headers = github_headers(
        {
            "User-Agent": "python",
            "Accept": "application/octet-stream",
        }
    )
    # API asset URL needs Accept: application/octet-stream
    api_url = manifest_asset.get("url")
    if api_url:
        headers["Accept"] = "application/octet-stream"
        url = api_url

    try:
        async with new_session(timeout=30) as client:
            res = await client.get(url, headers=headers)
            if res.status_code >= 400:
                log.warn(f"Failed to download build-manifest.json: HTTP {res.status_code}")
                return None, tag, release_id
            raw = res.content
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            data = json.loads(raw)
            manifest = Manifest.from_dict(data)
            log.success(f"Loaded previous build-manifest ({len(manifest.builds)} build(s)) from {tag}")
            return manifest, tag, release_id
    except Exception as e:
        log.warn(f"Could not parse previous build-manifest: {e}")
        return None, tag, release_id


def write_manifest(path: Path, manifest: Manifest) -> Path:
    """Write manifest to disk (for upload as a release asset)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info(f"Wrote {path} ({len(manifest.builds)} build(s))")
    return path


async def download_previous_asset(release_id: int, asset_name: str, dest: Path) -> Path | None:
    """Download a single asset from a known release id into dest."""
    try:
        assets = await get_assets(release_id)
    except Exception as e:
        log.warn(f"Could not list assets of release {release_id}: {e}")
        return None

    asset = next((a for a in assets if a.get("name") == asset_name or a.get("name") == asset_name.replace(" ", ".")), None)
    if not asset:
        log.warn(f"Asset {asset_name!r} not found on previous release {release_id}")
        return None

    url = asset.get("url")
    if not url:
        return None

    headers = github_headers(
        {
            "User-Agent": "python",
            "Accept": "application/octet-stream",
        }
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with new_session(timeout=settings.download_timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as res:
                if res.status_code >= 400:
                    log.warn(f"Download of {asset_name} failed: HTTP {res.status_code}")
                    return None
                with open(dest, "wb") as f:
                    async for chunk in res.aiter_content():
                        f.write(chunk)
        log.info(f"Copied previous asset: {asset_name} -> {dest}")
        return dest
    except Exception as e:
        log.warn(f"Failed to download previous asset {asset_name}: {e}")
        return None
