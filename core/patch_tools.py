from collections.abc import Callable
from pathlib import Path

from tenacity import retry, stop_after_attempt

from . import log
from . import retry as retry_conf
from .http import new_session
from .settings import settings

_GH_HEADERS = {
    "User-Agent": "python",
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
}


async def fetch_releases(owner: str, repo: str) -> list[dict]:
    """Reponun son 30 release'ini dondurur (draft olmayanlar)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=30"

    @retry(
        stop=stop_after_attempt(5),
        wait=retry_conf.exponential_with_jitter(max=30.0),
        before_sleep=retry_conf.before_sleep("GitHub request"),
        reraise=True,
    )
    async def _do():
        async with new_session(timeout=30) as client:
            res = await client.get(url, headers=_GH_HEADERS)
            if res.status_code >= 400:
                raise RuntimeError(f"GitHub API error: {res.status_code}")
            data = res.json()
            if not isinstance(data, list):
                raise RuntimeError("Unexpected GitHub API response")
            return [r for r in data if not r.get("draft")]

    return await _do()


async def fetch_latest_release(owner: str, repo: str, prerelease: bool = False) -> dict:
    releases = await fetch_releases(owner, repo)
    if not prerelease:
        stable = [r for r in releases if not r.get("prerelease")]
        if stable:
            releases = stable
    if not releases:
        raise RuntimeError("No releases found")
    return releases[0]


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


async def download_latest_github_asset(
    owner: str, repo: str, match: Callable[[str], bool], prerelease: bool = False
) -> dict:
    """Eslesen asset'i ararken SADECE son release'e bakma.

    Bazi patch kaynaklari (or. jasonwu/Gboard-patches) araya tema paketi
    gibi surumler koyabiliyor; bu durumda eslesen asset'i iceren EN YENI
    release secilir.
    """
    log.step(f"Fetching release: {owner}/{repo}")

    releases = await fetch_releases(owner, repo)

    candidates = releases
    if not prerelease:
        stable = [r for r in releases if not r.get("prerelease")]
        # Stabil release'te asset yoksa prerelease'lere geri dus
        candidates = stable if any(r.get("assets") for r in stable) else releases

    selected: tuple[dict, dict] | None = None
    for release in candidates:
        asset = next((a for a in release.get("assets") or [] if match(a["name"])), None)
        if asset:
            selected = (release, asset)
            break

    if not selected:
        raise RuntimeError(f"Matching asset not found in {owner}/{repo} (son {len(releases)} release tarandi)")

    release, asset = selected
    log.info(f"Selected: {asset['name']} (release: {release.get('tag_name')})")
    log.link(f"Indirme linki: <magenta>{asset['browser_download_url']}</magenta>")

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
