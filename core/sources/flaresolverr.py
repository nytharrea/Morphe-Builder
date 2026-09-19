"""Client for a local FlareSolverr instance (v3.5.2,
https://github.com/FlareSolverr/FlareSolverr) used to clear APKMirror's
Cloudflare challenge. FlareSolverrSharp (the other repo linked when this
migration was requested) is a .NET client for the same server - not usable
from this Python project, so this module talks to the server's own HTTP
API directly instead.

FlareSolverr drives a real browser and hands back the resulting page's
HTML, cookies and user-agent - it has no way to hand back a binary file a
"download" URL would trigger instead of a page. So the actual .apk/.apkm
bytes are always fetched afterwards with a plain HTTP client, replaying
the cookies and user-agent FlareSolverr just cleared for that origin.
"""

import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from .. import log
from .. import retry as retry_conf
from ..http import new_session
from ..settings import settings

SESSION_ID = "builder-morphe"


class FlareSolverrError(Exception):
    pass


class Cleared:
    def __init__(self, url: str, status: int, html: str, user_agent: str, cookies: list[dict]):
        self.url = url
        self.status = status
        self.html = html
        self.user_agent = user_agent
        self.cookies = cookies

    def cookie_jar(self) -> dict[str, str]:
        return {c["name"]: c["value"] for c in self.cookies if c.get("name") is not None}


async def _call(payload: dict) -> dict:
    async with AsyncSession(timeout=settings.flaresolverr_timeout + 15) as client:
        res = await client.post(settings.flaresolverr_url, json=payload)
        if res.status_code >= 400:
            raise FlareSolverrError(f"FlareSolverr HTTP {res.status_code}")
        data = res.json()
        if data.get("status") != "ok":
            raise FlareSolverrError(data.get("message") or "FlareSolverr reported failure")
        return data


_session_ready = False


@retry(
    stop=stop_after_attempt(8),
    wait=retry_conf.incrementing(start=2.0, increment=2.0, max=15.0),
    before_sleep=retry_conf.before_sleep("Waiting for FlareSolverr to accept requests"),
    retry=retry_if_exception_type(FlareSolverrError),
    reraise=True,
)
async def _create_session() -> None:
    await _call({"cmd": "sessions.create", "session": SESSION_ID})


async def ensure_session() -> None:
    global _session_ready
    if _session_ready:
        return
    await _create_session()
    _session_ready = True
    log.info("FlareSolverr session ready.")


async def close_session() -> None:
    global _session_ready
    if not _session_ready:
        return
    try:
        await _call({"cmd": "sessions.destroy", "session": SESSION_ID})
    except Exception as e:
        log.warn(f"Could not close FlareSolverr session cleanly: {e}")
    finally:
        _session_ready = False


@retry(
    stop=stop_after_attempt(4),
    wait=retry_conf.exponential_with_jitter(max=20.0),
    before_sleep=retry_conf.before_sleep("FlareSolverr request"),
    retry=retry_if_exception_type(FlareSolverrError),
    reraise=True,
)
async def get(url: str) -> Cleared:
    await ensure_session()
    data = await _call(
        {
            "cmd": "request.get",
            "url": url,
            "session": SESSION_ID,
            "maxTimeout": int(settings.flaresolverr_timeout * 1000),
        }
    )
    solution = data.get("solution") or {}
    return Cleared(
        url=solution.get("url") or url,
        status=solution.get("status") or 0,
        html=solution.get("response") or "",
        user_agent=solution.get("userAgent") or "",
        cookies=solution.get("cookies") or [],
    )


def _filename_from_headers(headers) -> str | None:
    disposition = headers.get("content-disposition")
    if not disposition:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    return match.group(1).strip() if match else None


def _filename_from_url(url: str, fallback: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    return name if name and "." in name else fallback


_BUNDLE_MARKERS = (("info.json", ".apkm"), ("manifest.json", ".xapk"), ("toc.pb", ".apks"))


def _detect_suffix(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None

    if "AndroidManifest.xml" in names:
        return ".apk"
    for marker, suffix in _BUNDLE_MARKERS:
        if marker in names:
            return suffix
    return ".apkm" if any(name.endswith(".apk") for name in names) else None


def _name_by_content(path: Path, fallback_name: str) -> Path:
    suffix = _detect_suffix(path)
    if suffix is None:
        return path

    target = path.with_name(Path(fallback_name).stem + suffix)
    if target != path:
        path.replace(target)
    return target


async def download_file(url: str, cleared: Cleared, out_dir: Path, fallback_name: str) -> Path:
    headers = {"User-Agent": cleared.user_agent} if cleared.user_agent else {}
    async with (
        new_session(timeout=None, follow_redirects=True, impersonate="chrome") as client,
        client.stream("GET", url, headers=headers, cookies=cleared.cookie_jar()) as res,
    ):
        if res.status_code >= 400:
            raise FlareSolverrError(f"File download failed: HTTP {res.status_code}")

        filename = _filename_from_headers(res.headers) or _filename_from_url(url, fallback_name)
        out_path = out_dir / filename

        with open(out_path, "wb") as f:
            async for chunk in res.aiter_content():
                f.write(chunk)

    return _name_by_content(out_path, fallback_name)
