"""APKMirror indirme kaynagi - FlareSolverr + duz HTTP.

Eski Camoufox/Playwright tarayici kaldirildi. Akis:
1. curl_cffi (firefox impersonation) ile sayfa istenir.
2. Cloudflare challenge gorulurse FlareSolverr cozer; cf_clearance cerezi
   ve user-agent mevcut HTTP oturumuna islenir.
3. Tum gezinme selectolax ile HTML parse edilir (JS evaluate yok).
"""

import asyncio
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from selectolax.parser import HTMLParser
from tenacity import retry, stop_after_attempt

from .. import log
from .. import retry as retry_conf
from ..apk.versions import to_apkmirror_version
from ..flaresolverr import FlareSolverrClient
from ..http import new_session
from ..settings import settings

APP_SITES = {
    "youtube": {"org": "google-inc", "slug": "youtube"},
    "youtube-music": {"org": "google-inc", "slug": "youtube-music"},
    "reddit": {"org": "reddit-inc", "slug": "reddit"},
    "twitter": {"org": "x-corp", "slug": "twitter", "release_slug": "x"},
    "instagram": {"org": "instagram", "slug": "instagram"},
    "gboard": {"org": "google-inc", "slug": "gboard", "release_slug": "gboard-the-google-keyboard"},
    "speedtest": {"org": "ookla", "slug": "speedtest"},
    "brave": {"org": "brave-software", "slug": "brave-browser", "release_slug": "brave-private-web-browser-vpn"},
    "proton-vpn": {
        "org": "proton-technologies-ag",
        "slug": "protonvpn-secure-and-free-vpn",
        "release_slug": "proton-vpn-fast-secure-vpn",
    },
    "tiktok": {"org": "tiktok-pte-ltd", "slug": "tik-tok-including-musical-ly", "release_slug": "tiktok"},
    "warp": {
        "org": "cloudflare",
        "slug": "1-1-1-1-faster-safer-internet",
        "release_slug": "1-1-1-1-warp-safer-internet",
    },
    "inshot": {
        "org": "inshot-inc",
        "slug": "inshot-video-editor-photo-editor",
        "release_slug": "video-editor-maker-inshot",
    },
    "google-photos": {"org": "google-inc", "slug": "photos", "release_slug": "google-photos"},
    "proton-pass": {"org": "proton-technologies-ag", "slug": "proton-pass-password-manager"},
    "notesnook": {
        "org": "streetwriters-private-limited",
        "slug": "notesnook-private-notes-app",
        "release_slug": "notesnook-secure-private-notes",
    },
    # NOT: termius kaldirildi (artik patchlenmiyor).
}

_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required! | cloudflare",
    "verify you are human",
    "cf-browser-verification",
    "cf_chl_",
    "ddos protection by cloudflare",
    "performing security verification",
    "verifies you are not a bot",
)

_VERSION_SLUG_RE = re.compile(r"-((?:\d+-)+(?:\d+[a-z0-9]*(?:-[a-z]+\.\d+)?))-release")
_DOWNLOAD_HREF_RE = re.compile(r"\.apk(?:m)?(?:$|\?)|download\.php", re.IGNORECASE)


def _looks_like_challenge(status_code: int, html: str) -> bool:
    if status_code in (403, 503):
        return True
    head = html[:2000].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _page_is_404(tree: HTMLParser) -> bool:
    title = tree.css_first("title")
    if title is None:
        return False
    text = title.text().lower()
    return "404" in text and ("not be found" in text or "whoops" in text)


class APKMirrorClient:
    def __init__(self) -> None:
        base = settings.apkmirror_base_url.rstrip("/")
        self._base = base
        self._http = new_session(follow_redirects=True, timeout=60)
        self._http.headers["Referer"] = f"{base}/"
        self._flaresolverr = FlareSolverrClient()
        self._last_request = 0.0

    async def __aenter__(self) -> "APKMirrorClient":
        await self._flaresolverr.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._flaresolverr.close()

    async def _throttle(self) -> None:
        """APKMirror'i korumak icin istekler arasi minimum 2-4 sn."""
        elapsed = time.monotonic() - self._last_request
        wait = random.uniform(2.0, 4.0) - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    async def _apply_clearance(self, url: str) -> None:
        solution = await self._flaresolverr.solve_get(url)
        ua = solution.get("userAgent")
        if ua:
            self._http.headers["User-Agent"] = ua
        for cookie in solution["cookies"]:
            domain = (cookie.get("domain") or ".apkmirror.com").lstrip(".")
            self._http.cookies.set(cookie["name"], cookie["value"], domain=domain)

    @retry(
        stop=stop_after_attempt(4),
        wait=retry_conf.exponential_with_jitter(max=30.0),
        before_sleep=retry_conf.before_sleep("APKMirror istegi"),
        reraise=True,
    )
    async def get_html(self, url: str, *, referer: str | None = None) -> str:
        await self._throttle()
        headers = {"Referer": referer} if referer else {}
        res = await self._http.get(url, headers=headers)
        self._last_request = time.monotonic()
        html = res.text

        if _looks_like_challenge(res.status_code, html):
            log.warn(f"Cloudflare challenge: {url}")
            await self._apply_clearance(url)
            await self._throttle()
            res = await self._http.get(url, headers=headers)
            self._last_request = time.monotonic()
            html = res.text
            if _looks_like_challenge(res.status_code, html):
                raise RuntimeError(f"FlareSolverr sonrasi challenge devam ediyor: {url}")

        if res.status_code == 404:
            return html  # 404 sayfasi: arayan karar versin
        if res.status_code >= 400:
            raise RuntimeError(f"APKMirror HTTP {res.status_code}: {url}")
        return html

    # ---------- surum cozumleme ----------

    async def get_latest_listing(self, app_name: str) -> dict | None:
        site = APP_SITES[app_name]
        url = f"{self._base}/apk/{site['org']}/{site['slug']}/"
        log.search(f"APKMirror liste sayfasi: {url}")
        tree = HTMLParser(await self.get_html(url))
        for anchor in tree.css("a[href*='-release/']"):
            href = anchor.attributes.get("href") or ""
            match = _VERSION_SLUG_RE.search(href)
            if match:
                version = match.group(1).replace("-", ".")
                return {"version": version, "url": urljoin(self._base, href)}
        return None

    async def _resolve_list_url(self, site: dict, version: str) -> str:
        version_slug = to_apkmirror_version(version)
        name_part = site.get("release_slug") or site["slug"]
        folder_url = f"{self._base}/apk/{site['org']}/{site['slug']}"

        candidates = [
            f"{folder_url}/{name_part}-{version_slug}-release/",
            f"{folder_url}/{name_part}-{version_slug}-release-0-release/",
            f"{folder_url}/{name_part}-{version_slug}-beta-0-release/",
            f"{folder_url}/{name_part}-{version_slug}-beta-1-release/",
        ]
        for candidate in candidates:
            log.search(f"TRY: {candidate}")
            tree = HTMLParser(await self.get_html(candidate))
            if not _page_is_404(tree) and tree.css(".variants-table .table-row"):
                return candidate

        log.search("Direkt eslesme yok, liste sayfasinda taraniyor...")
        tree = HTMLParser(await self.get_html(f"{folder_url}/"))
        slug_part = f"-{version_slug}-"
        for anchor in tree.css("a[href*='-release/']"):
            href = anchor.attributes.get("href") or ""
            if slug_part in href and "#" not in href:
                return urljoin(self._base, href)

        raise RuntimeError(f"APKMirror'da {version} surumu icin sayfa bulunamadi")

    async def _extract_variant_url(self, html: str, app_name: str, force_build: str | None) -> str | None:
        """Eski JS slot mantiginin Python portu: nodpi > anydpi > diger,
        APK > bundle (instagram haric; o bundle/.apkm zorunlu)."""
        allowed_archs = ("universal", "evrensel", "noarch", "arm64-v8a")
        tree = HTMLParser(html)
        candidates: list[str | None] = [None] * 6

        for row in tree.css(".variants-table .table-row"):
            cells = row.css(".table-cell")
            if len(cells) < 4:
                continue
            link = cells[0].css_first("a.accent_color")
            if link is None:
                continue
            name_text = cells[0].text()
            if force_build and force_build not in name_text:
                continue
            badge = cells[0].css_first(".apkm-badge")
            is_bundle = bool(badge and "bundle" in badge.text().lower())
            if app_name == "instagram" and not is_bundle:
                continue
            arch_text = cells[1].text().lower().strip()
            if arch_text and not any(a in arch_text for a in allowed_archs):
                continue
            dpi_text = cells[3].text().lower().strip()
            if dpi_text == "" or "nodpi" in dpi_text:
                slot = 3 if is_bundle else 0
            elif "anydpi" in dpi_text:
                slot = 4 if is_bundle else 1
            else:
                slot = 5 if is_bundle else 2
            if candidates[slot] is None:
                candidates[slot] = urljoin(self._base, link.attributes["href"])

        return next((c for c in candidates if c), None)

    async def _find_download_link(self, page_url: str) -> str:
        html = await self.get_html(page_url, referer=page_url.rsplit("/", 2)[0] + "/")
        tree = HTMLParser(html)
        button = tree.css_first("a.downloadButton")
        if button is None:
            raise RuntimeError(f"downloadButton bulunamadi: {page_url}")
        confirm_url = urljoin(page_url, button.attributes["href"])
        await self._throttle()
        res = await self._http.get(confirm_url)
        tree = HTMLParser(res.text)
        for anchor in tree.css("a[href]"):
            href = anchor.attributes.get("href") or ""
            if _DOWNLOAD_HREF_RE.search(href):
                return urljoin(self._base, href)
        raise RuntimeError(f"Indirme baglantisi bulunamadi: {confirm_url}")

    async def download_apk(self, version: str, app_name: str, force_build: str | None = None) -> str:
        site = APP_SITES[app_name]
        log.step(f"APKMirror cozumleniyor: {app_name.upper()} v{version}")

        list_url = await self._resolve_list_url(site, version)
        variant_url = await self._extract_variant_url(await self.get_html(list_url), app_name, force_build)
        if variant_url is None:
            raise RuntimeError(f"Uygun variant bulunamadi ({app_name} v{version})")
        file_url = await self._find_download_link(variant_url)

        out_dir = Path.cwd() / "downloads"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(file_url.split("?")[0]).name or f"{app_name}-{version}.apk"
        file_path = out_dir / filename

        log.download(f"Indiriliyor: {filename}")
        await self._throttle()
        async with self._http.stream("GET", file_url) as res:
            if res.status_code >= 400:
                raise RuntimeError(f"Indirme HTTP {res.status_code}")
            with open(file_path, "wb") as f:
                async for chunk in res.aiter_content():
                    f.write(chunk)

        size = file_path.stat().st_size
        if size < 1024:
            file_path.unlink(missing_ok=True)
            raise RuntimeError(f"Indirilen dosya cok kucuk ({size} bayt)")
        log.success(f"Indirildi: {file_path} ({size / 1024 / 1024:.1f} MB)")
        return str(file_path)


_client: APKMirrorClient | None = None


async def _get_client() -> APKMirrorClient:
    global _client
    if _client is None:
        _client = APKMirrorClient()
        await _client._flaresolverr.start()
    return _client


async def get_latest_listing(app_name: str) -> dict | None:
    return await (await _get_client()).get_latest_listing(app_name)


async def download_apk(version: str, app_name: str, force_build: str | None = None) -> str:
    return await (await _get_client()).download_apk(version, app_name, force_build)


async def close_browser() -> None:  # API geriye uyumluluk
    global _client
    if _client is not None:
        await _client.close()
        _client = None
