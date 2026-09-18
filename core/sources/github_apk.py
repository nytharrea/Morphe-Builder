"""GitHub Releases kaynagi - artik sadece DIRECT_REPOS (inure) icin.

instagram ve speedtest artik APKMirror'dan cekiliyor (APP_TAGS kaldirildi).
"""

from pathlib import Path

from curl_cffi.requests import AsyncSession

from .. import log
from ..http import new_session
from ..settings import settings

_GH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Python)",
    "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
}

DIRECT_REPOS = {
    "inure-github": ("Hamza417", "Inure", "github", "build{version}"),
    "inure-play": ("Hamza417", "Inure", "play", "build{version}"),
}


def _build_tag(tag_template: str, version: str) -> str:
    prefix = tag_template.split("{version}")[0]
    if prefix and version.startswith(prefix):
        return version
    return tag_template.format(version=version)


def _pick_apk_asset(assets: list[dict], name_hint: str | None = None) -> dict | None:
    candidates = [a for a in assets if a["name"].endswith(".apk") or a["name"].endswith(".apkm")]
    if not candidates:
        return None
    if name_hint:
        hinted = [a for a in candidates if name_hint.lower() in a["name"].lower()]
        if hinted:
            candidates = hinted
    arm64 = next((a for a in candidates if "arm64" in a["name"].lower()), None)
    return arm64 or candidates[0]


async def _download_asset(client: AsyncSession, asset: dict) -> str:
    size_mb = asset["size"] / (1024 * 1024)
    log.download(f"Found file to download: {asset['name']} ({size_mb:.2f} MB)")
    out_dir = Path(__file__).resolve().parent.parent.parent / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    file_path = out_dir / asset["name"]
    async with client.stream("GET", asset["browser_download_url"]) as file_res:
        if file_res.status_code >= 400:
            raise RuntimeError("Failed to download file from GitHub!")
        with open(file_path, "wb") as f:
            async for chunk in file_res.aiter_content():
                f.write(chunk)
    if Path(file_path).stat().st_size < 1024:
        raise RuntimeError("Downloaded file is too small - likely an error page")
    log.success(f"Done: {file_path}")
    return str(file_path)


async def download_apk(version: str, app_name: str, force_build: str | None = None) -> str:
    if app_name not in DIRECT_REPOS:
        raise RuntimeError(f"{app_name} artik GitHub kaynagindan cekilmiyor (APKMirror kullanin)")
    owner, repo, name_hint, tag_template = DIRECT_REPOS[app_name]

    async with new_session(timeout=30, follow_redirects=True) as client:
        release_data = None
        if version and version != "latest":
            wanted_tag = _build_tag(tag_template, version)
            log.step(f"Fetching info from GitHub: {app_name.upper()} ({owner}/{repo}, tag: {wanted_tag})")
            res = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{wanted_tag}",
                headers=_GH_HEADERS,
            )
            if res.status_code < 400:
                release_data = res.json()
            else:
                log.warn(f'Tag "{wanted_tag}" not found, falling back to latest release.')

        if release_data is None:
            log.step(f"Fetching info from GitHub: {app_name.upper()} ({owner}/{repo}, latest release)")
            res = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
                headers=_GH_HEADERS,
            )
            if res.status_code >= 400:
                raise RuntimeError(f"GitHub API error: {res.status_code}")
            release_data = res.json()

        asset = _pick_apk_asset(release_data.get("assets") or [], name_hint)
        if not asset:
            raise RuntimeError(f'No .apk or .apkm file found in "{owner}/{repo}".')
        log.link(f"Indirme linki: {asset['browser_download_url']}")
        return await _download_asset(client, asset)
