import asyncio
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from . import log
from .fetchers.release_assets import download_latest_release_asset
from .http import github_headers, new_session
from .settings import settings


def _headers() -> dict[str, str]:
    """Built fresh per-call (not a module-level constant) so it always
    reflects the current settings.github_token, and reuses http.py's
    github_headers() so a missing token omits Authorization entirely
    instead of sending a malformed empty "Bearer " value."""
    return github_headers({"User-Agent": "python", "Accept": "application/vnd.github+json"})


def _assert_configured():
    if not settings.github_token.get_secret_value():
        raise RuntimeError("Missing GITHUB_TOKEN")
    if not settings.github_repository:
        raise RuntimeError("Missing GITHUB_REPOSITORY")


def _asset_name(file_name: str) -> str:
    """GitHub release asset names: spaces become dots (and we standardize on that)."""
    return Path(file_name).name.replace(" ", ".")


def _names_match(a: str, b: str) -> bool:
    """Match asset names allowing space/dot variants."""
    if a == b:
        return True
    return a.replace(" ", ".") == b.replace(" ", ".")


async def get_release_by_tag(tag: str) -> dict | None:
    """Return the release for the given tag, or None if it does not exist."""
    _assert_configured()
    async with new_session(timeout=30) as client:
        res = await client.get(
            f"https://api.github.com/repos/{settings.github_repository}/releases/tags/{tag}",
            headers=_headers(),
        )
        if res.status_code == 404:
            return None
        data = res.json()
        if "id" not in data:
            return None
        return data


async def create_new_release(tag: str, release_name: str, release_body: str = "", draft: bool = False) -> dict:
    """Create a release, or return + update the existing one if the tag already exists.

    This makes finalize re-runs safe: previous runs may have created the tag/release
    without successfully uploading APKs.
    """
    _assert_configured()

    existing = await get_release_by_tag(tag)
    if existing:
        log.warn(f"Release already exists for tag {tag} (id={existing['id']}), reusing it")
        async with new_session(timeout=30) as client:
            res = await client.patch(
                f"https://api.github.com/repos/{settings.github_repository}/releases/{existing['id']}",
                headers=_headers(),
                json={
                    "name": release_name,
                    "body": release_body,
                    "draft": draft,
                    "prerelease": False,
                    "make_latest": "false" if draft else "true",
                },
            )
            data = res.json()
        if "id" not in data:
            raise RuntimeError(f"Failed to update existing release: {data}")
        return data

    log.step(f"Creating new release: {tag}")
    async with new_session(timeout=30) as client:
        res = await client.post(
            f"https://api.github.com/repos/{settings.github_repository}/releases",
            headers=_headers(),
            json={
                "tag_name": tag,
                "name": release_name,
                "body": release_body,
                "draft": draft,
                "prerelease": False,
                "make_latest": "false" if draft else "true",
            },
        )
        data = res.json()

    if "id" not in data:
        raise RuntimeError(f"Failed to create release: {data}")

    return data


async def get_latest_release(*, include_drafts: bool = False) -> dict | None:
    """Return the most recently published release (by published_at / created_at).

    Drafts are skipped by default so a half-finished finalize does not become
    the "previous" state for the next prepare run.
    """
    _assert_configured()
    releases = await list_releases()
    candidates = [r for r in releases if include_drafts or not r.get("draft")]
    if not candidates:
        return None

    def _sort_key(r: dict) -> str:
        return r.get("published_at") or r.get("created_at") or ""

    return max(candidates, key=_sort_key)


async def list_releases() -> list[dict]:
    async with new_session(timeout=30) as client:
        res = await client.get(
            f"https://api.github.com/repos/{settings.github_repository}/releases",
            headers=_headers(),
            params={"per_page": 100},
        )
        data = res.json()

    if not isinstance(data, list):
        raise RuntimeError(f"Failed to list releases: {data}")

    return data


async def delete_release(release_id: int) -> None:
    async with new_session(timeout=30) as client:
        await client.delete(
            f"https://api.github.com/repos/{settings.github_repository}/releases/{release_id}",
            headers=_headers(),
        )


async def delete_tag(tag: str) -> None:
    async with new_session(timeout=30) as client:
        await client.delete(
            f"https://api.github.com/repos/{settings.github_repository}/git/refs/tags/{tag}",
            headers=_headers(),
        )


async def delete_other_releases(keep_release_id: int) -> None:
    releases = await list_releases()

    for release in releases:
        if release["id"] == keep_release_id:
            continue
        log.warn(f"Deleting old release: {release.get('tag_name')}")
        await delete_release(release["id"])
        await delete_tag(release["tag_name"])


async def update_release_body(release_id: int, body: str) -> dict:
    async with new_session(timeout=30) as client:
        res = await client.patch(
            f"https://api.github.com/repos/{settings.github_repository}/releases/{release_id}",
            headers=_headers(),
            json={"body": body},
        )
        return res.json()


async def get_assets(release_id: int) -> list[dict]:
    """List ALL release assets (paginated). GitHub defaults to 30/page."""
    all_assets: list[dict] = []
    page = 1
    async with new_session(timeout=30) as client:
        while True:
            res = await client.get(
                f"https://api.github.com/repos/{settings.github_repository}/releases/{release_id}/assets",
                headers=_headers(),
                params={"per_page": 100, "page": page},
            )
            data = res.json()
            if not isinstance(data, list):
                raise RuntimeError(f"Failed to list assets: {data}")
            if not data:
                break
            all_assets.extend(data)
            if len(data) < 100:
                break
            page += 1
    return all_assets


async def delete_asset(asset_id: int) -> None:
    async with new_session(timeout=30) as client:
        res = await client.delete(
            f"https://api.github.com/repos/{settings.github_repository}/releases/assets/{asset_id}",
            headers=_headers(),
        )
        # 204 No Content on success; 404 is fine (already gone)
        if res.status_code not in (204, 404):
            try:
                detail = res.json()
            except Exception:
                detail = res.text
            raise RuntimeError(f"Failed to delete asset {asset_id}: status={res.status_code} {detail}")


def _iter_file_chunks(file_path: str, chunk_size: int = 8 * 1024 * 1024):
    """Yield file contents in chunks so large APKs are not fully buffered in Python."""
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


async def _upload(upload_url: str, file_path: str, asset_name: str) -> dict:
    """Upload a single asset to a GitHub Release.

    GitHub requires a correct Content-Length. Streaming without it causes
    "Bad Content-Length" (HTTP 400). We stream in chunks AND set Content-Length
    from the file size so uploads stay parallel/fast and memory-friendly.

    asset_name must not contain spaces (use dots) — GitHub normalizes spaces to dots.
    """
    file_size = Path(file_path).stat().st_size

    url = upload_url.replace("{?name,label}", "") + f"?name={quote(asset_name)}"

    async with new_session(timeout=None) as client:
        res = await client.post(
            url,
            headers={
                **_headers(),
                "Content-Type": "application/vnd.android.package-archive",
                "Content-Length": str(file_size),
            },
            content=_iter_file_chunks(file_path),
        )
        data = res.json()

        # GitHub asset upload returns 201 Created on success
        if res.status_code not in (200, 201):
            raise RuntimeError(
                f"Upload failed for {asset_name} (size={file_size} bytes, status={res.status_code}): {data}"
            )

        # Sanity check: uploaded asset should report the same size
        uploaded_size = data.get("size")
        if uploaded_size is not None and uploaded_size != file_size:
            raise RuntimeError(
                f"Upload size mismatch for {asset_name}: "
                f"local={file_size} bytes, github={uploaded_size} bytes. "
                f"Response: {data}"
            )

        return data


def _is_already_exists_error(exc: BaseException) -> bool:
    return "already_exists" in str(exc)


async def _delete_asset_by_name(release_id: int, asset_name: str) -> bool:
    """Delete any asset whose name matches asset_name (space/dot tolerant)."""
    assets = await get_assets(release_id)
    existing = next((a for a in assets if _names_match(a["name"], asset_name)), None)
    if not existing:
        return False
    log.warn(f"Replacing existing asset: {existing['name']} (id={existing['id']})")
    await delete_asset(existing["id"])
    await asyncio.sleep(0.5)
    return True


async def upload_with_replace(release: dict, file_path: str):
    local_name = Path(file_path).name
    asset_name = _asset_name(local_name)  # spaces -> dots
    file_size = Path(file_path).stat().st_size
    release_id = release["id"]

    await _delete_asset_by_name(release_id, asset_name)

    log.download(f"Uploading: {asset_name} ({file_size / (1024 * 1024):.1f} MB)")

    for attempt in range(3):
        try:
            result = await _upload(release["upload_url"], file_path, asset_name)
            break
        except RuntimeError as e:
            if not _is_already_exists_error(e) or attempt == 2:
                raise
            log.warn(
                f"Asset already exists during upload (attempt {attempt + 1}/3), "
                f"deleting and retrying: {asset_name}"
            )
            deleted = await _delete_asset_by_name(release_id, asset_name)
            if not deleted:
                names = [a["name"] for a in await get_assets(release_id)]
                log.warn(f"Could not find {asset_name} in assets list ({len(names)} assets): {names[:30]}")
            await asyncio.sleep(1.0 * (attempt + 1))
    else:
        raise RuntimeError(f"Upload failed for {asset_name} after retries")

    uploaded_size = result.get("size", file_size)
    log.info(f"✅ Uploaded {asset_name} ({uploaded_size / (1024 * 1024):.1f} MB, id={result.get('id')})")
    return result


async def upload_patched_apk(release: dict, apk_path: str):
    _assert_configured()
    await upload_with_replace(release, apk_path)


async def upload_patched_apks(release: dict, apk_paths: list[str]) -> None:
    _assert_configured()
    semaphore = asyncio.Semaphore(settings.upload_concurrency)

    async def _upload_one(path: str) -> None:
        async with semaphore:
            await upload_with_replace(release, path)

    await asyncio.gather(*(_upload_one(path) for path in apk_paths))


async def _fetch_and_upload_companion(
    release: dict, owner: str, repo: str, match: Callable[[str], bool], base_name: str
) -> None:
    result = await download_latest_release_asset(owner=owner, repo=repo, match=match, prerelease=True)

    final_name = base_name.replace(".apk", "-PRERELEASE.apk") if result.get("prerelease") else base_name
    final_name = _asset_name(final_name)

    original_path = Path.cwd() / result["name"]
    new_path = Path.cwd() / final_name
    if original_path.exists() and original_path != new_path:
        original_path.rename(new_path)

    if result.get("prerelease"):
        log.warn(f"{base_name} latest release ({result['tag']}) is a PRERELEASE")

    assets = await get_assets(release["id"])
    if any(_names_match(a["name"], final_name) for a in assets):
        log.info(f"{final_name} already up to date on this release, skipping upload")
        return

    await upload_with_replace(release, str(new_path))


async def upload_microg_once(release: dict):
    _assert_configured()

    log.step("Fetching MicroG...")
    await asyncio.gather(
        _fetch_and_upload_companion(
            release,
            "MorpheApp",
            "MicroG-RE",
            lambda n: n.endswith("-arm64-v8a.apk") and "noicon" not in n.lower(),
            "MicroG.apk",
        ),
        _fetch_and_upload_companion(
            release,
            "MorpheApp",
            "MicroG-RE",
            lambda n: n.endswith("-noicon-arm64-v8a.apk"),
            "MicroG-NoIcon.apk",
        ),
    )


async def upload_pothelper_once(release: dict):
    _assert_configured()

    log.step("Fetching PotHelper...")
    await _fetch_and_upload_companion(
        release,
        "MorpheApp",
        "PotHelper",
        lambda n: n.endswith(".apk"),
        "PotHelper.apk",
    )
