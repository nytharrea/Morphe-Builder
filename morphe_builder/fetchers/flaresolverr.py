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

SESSION_ID = "morphe-builder"


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


def _filename_from_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    return match.group(1).strip() if match else None


def _filename_from_headers(headers) -> str | None:
    return _filename_from_disposition(headers.get("content-disposition"))


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
    """Streams `url` into out_dir, replaying the cookies/user-agent
    FlareSolverr just cleared. Beyond a plain stream-to-disk, this:

    - uses a bounded timeout (`settings.download_timeout`) instead of
      waiting forever on a connection that stalls mid-download
    - writes to a "<name>.part" temp file and only renames it into place
      once the download is verified, so a failed/interrupted attempt never
      leaves a corrupt file sitting at the real output path
    - checks the byte count against Content-Length when the server sends
      one, to catch a connection that closed early without an error
    - since every format this ever saves (.apk/.apkm/.xapk/.apks) is a zip
      archive, confirms the file actually opens as one and that every
      entry in it passes its checksum - catching a download that's
      truncated-but-plausible-sized (an HTML error/CAPTCHA page saved
      under a .apk name, say) before it ever reaches the patcher
    - retries itself a few times on any of the above before giving up, so
      a short-lived network blip doesn't waste the whole Cloudflare-clear
      that got us this download link in the first place. This is a
      separate, smaller retry from apkmirror.py's own - that one re-clears
      a fresh page/session on a Cloudflare challenge; this one just
      re-runs the same already-cleared download a couple of times.
    """
    headers = {"User-Agent": cleared.user_agent} if cleared.user_agent else {}
    cookies = cleared.cookie_jar()
    temp_path = out_dir / f"{Path(fallback_name).stem}.part"

    @retry(
        stop=stop_after_attempt(3),
        wait=retry_conf.exponential_with_jitter(max=10.0),
        before_sleep=retry_conf.before_sleep(f"Downloading {fallback_name}"),
        reraise=True,
    )
    async def _attempt() -> str | None:
        downloaded = 0
        content_disposition: str | None = None

        try:
            async with (
                new_session(
                    timeout=settings.download_timeout, follow_redirects=True, impersonate="chrome"
                ) as client,
                client.stream("GET", url, headers=headers, cookies=cookies) as res,
            ):
                if res.status_code >= 400:
                    raise FlareSolverrError(f"File download failed: HTTP {res.status_code}")

                content_disposition = res.headers.get("content-disposition")
                expected_size = res.headers.get("content-length")

                with open(temp_path, "wb") as f:
                    async for chunk in res.aiter_content():
                        f.write(chunk)
                        downloaded += len(chunk)

                if expected_size is not None and downloaded != int(expected_size):
                    raise FlareSolverrError(f"Download incomplete: got {downloaded} of {expected_size} bytes")

            if downloaded < 1024:
                raise FlareSolverrError(f"Downloaded file too small ({downloaded} bytes)")

            if not zipfile.is_zipfile(temp_path):
                raise FlareSolverrError("Downloaded file is not a valid archive (corrupt or incomplete download)")

            with zipfile.ZipFile(temp_path) as archive:
                bad_entry = archive.testzip()
            if bad_entry is not None:
                raise FlareSolverrError(f"Downloaded archive is corrupt (bad entry: {bad_entry})")
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        return content_disposition

    content_disposition = await _attempt()

    filename = _filename_from_disposition(content_disposition) or _filename_from_url(url, fallback_name)
    out_path = out_dir / filename
    temp_path.replace(out_path)

    return _name_by_content(out_path, fallback_name)
