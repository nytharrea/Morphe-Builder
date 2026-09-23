from collections.abc import Callable
from pathlib import Path

from tenacity import retry, stop_after_attempt

from .. import log
from .. import retry as retry_conf
from ..http import github_headers, new_session


def _select_release(releases: list[dict], match: Callable[[str], bool] | None = None) -> dict | None:
    for release in releases:
        if release.get("draft"):
            continue
        if match is None or any(match(a["name"]) for a in release.get("assets") or []):
            return release
        log.notice(f"Skipping release {release.get('tag_name')}: no matching asset")
    return None


async def fetch_latest_release(
    owner: str, repo: str, prerelease: bool = False, match: Callable[[str], bool] | None = None
) -> dict:
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/releases"
        if prerelease
        else f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    )

    @retry(
        stop=stop_after_attempt(5),
        wait=retry_conf.exponential_with_jitter(max=30.0),
        before_sleep=retry_conf.before_sleep("GitHub request"),
        reraise=True,
    )
    async def _do():
        async with new_session(timeout=30) as client:
            res = await client.get(
                url,
                headers=github_headers({"User-Agent": "python", "Accept": "application/vnd.github+json"}),
            )
            if res.status_code >= 400:
                raise RuntimeError(f"GitHub API error: {res.status_code} ({owner}/{repo})")

            return res.json()

    data = await _do()

    if not prerelease:
        return data

    release = _select_release(data, match) if isinstance(data, list) else None
    if release is None:
        raise RuntimeError(f"No release with a matching asset found in {owner}/{repo}")

    return release


async def _download_file(url: str, output_path: Path, expected_size: int | None = None) -> str:
    temp_path = output_path.with_name(output_path.name + ".part")
    downloaded = temp_path.stat().st_size if temp_path.exists() else 0

    headers = {"User-Agent": "python", "Accept": "*/*"}
    if downloaded > 0:
        headers["Range"] = f"bytes={downloaded}-"
        log.download(f"Resuming at {downloaded} bytes")

    mode = "ab" if downloaded > 0 else "wb"

    async with (
        new_session(follow_redirects=True, timeout=None) as client,
        client.stream("GET", url, headers=headers) as res,
    ):
        if res.status_code >= 400:
            raise RuntimeError(f"HTTP {res.status_code}")

        with open(temp_path, mode) as f:
            async for chunk in res.aiter_content():
                f.write(chunk)
                downloaded += len(chunk)

    if expected_size and downloaded != expected_size:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Size mismatch: {downloaded}/{expected_size}")

    temp_path.rename(output_path)
    return str(output_path)


async def download_latest_release_asset(
    owner: str, repo: str, match: Callable[[str], bool], prerelease: bool = False
) -> dict:
    log.step(f"Fetching release: {owner}/{repo}")

    release = await fetch_latest_release(owner, repo, prerelease, match)

    assets = release.get("assets") or []
    if not assets:
        raise RuntimeError(f"Repo {owner}/{repo} has no assets")

    asset = next((a for a in assets if match(a["name"])), None)
    if not asset:
        raise RuntimeError("Matching asset not found")

    log.info(f"Selected: {asset['name']} ({release.get('tag_name')})")

    out_path = Path(asset["name"])

    if out_path.exists():
        size = out_path.stat().st_size
        if size < 1024:
            log.warn("Removing corrupt cache")
            out_path.unlink()
        else:
            log.info(f"Using cached file: {asset['name']}")
            return {
                "name": asset["name"],
                "body": release.get("body") or "",
                "tag": release.get("tag_name") or "",
                "prerelease": bool(release.get("prerelease")),
            }

    @retry(
        stop=stop_after_attempt(5),
        wait=retry_conf.exponential_with_jitter(max=30.0),
        before_sleep=retry_conf.before_sleep("GitHub download"),
        reraise=True,
    )
    async def _do():
        await _download_file(asset["browser_download_url"], out_path, asset.get("size"))

    await _do()

    log.success(f"Done: {asset['name']}")

    return {
        "name": asset["name"],
        "body": release.get("body") or "",
        "tag": release.get("tag_name") or "",
        "prerelease": bool(release.get("prerelease")),
    }


download_latest_github_asset = download_latest_release_asset
