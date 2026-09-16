import asyncio
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from tenacity import AsyncRetrying, retry, retry_if_exception_type, stop_after_attempt
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from .. import log
from .. import retry as retry_conf
from ..apk.versions import to_apkmirror_version
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
    "termius": {
        "org": "termius-corporation",
        "slug": "termius-ssh-telnet-client",
        "release_slug": "termius-modern-ssh-client",
    },
}

DIAGNOSTICS_DIR = Path(__file__).resolve().parent.parent.parent / "diagnostics"

RESOLVE_BUDGET_SECONDS = 300.0

_CHALLENGE_MARKERS = [
    "just a moment",
    "checking your browser",
    "attention required! | cloudflare",
    "verify you are human",
    "cf-browser-verification",
    "cf_chl_",
    "ddos protection by cloudflare",
    "performing security verification",
    "verifies you are not a bot",
]

# FlareSolverr session state (shared for the whole run so clearance cookies stick)
_fs_session_id: str | None = None
_fs_cookies: dict[str, str] = {}
_fs_user_agent: str | None = None
_challenge_hits = 0
_cooldown_until = 0.0


async def _jitter_sleep(base: float, spread: float = 0.6) -> None:
    await asyncio.sleep(base + random.uniform(0, spread))


def _flaresolverr_url() -> str:
    return settings.flaresolverr_url.rstrip("/")


async def _fs_request(cmd: str, **payload: Any) -> dict[str, Any]:
    """POST a command to the FlareSolverr /v1 endpoint and return the JSON body."""
    body = {"cmd": cmd, **payload}
    async with new_session(timeout=120) as session:
        resp = await session.post(
            f"{_flaresolverr_url()}/v1",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"FlareSolverr error: {data.get('message', data)}")
        return data


@retry(
    stop=stop_after_attempt(6),
    wait=retry_conf.incrementing(start=1.5, increment=1.5, max=8.0),
    before_sleep=retry_conf.before_sleep("Could not create FlareSolverr session"),
    reraise=True,
)
async def _ensure_session() -> str:
    global _fs_session_id
    if _fs_session_id is not None:
        return _fs_session_id
    log.info("Creating FlareSolverr session...")
    data = await _fs_request("sessions.create")
    session_id = data.get("session")
    if not session_id:
        raise RuntimeError(f"FlareSolverr sessions.create returned no session id: {data}")
    _fs_session_id = session_id
    log.info(f"FlareSolverr session ready: {session_id}")
    return session_id


async def close_browser():
    """Destroy the FlareSolverr session if one was created (name kept for main.py compat)."""
    global _fs_session_id, _fs_cookies, _fs_user_agent
    if _fs_session_id is not None:
        try:
            await _fs_request("sessions.destroy", session=_fs_session_id)
            log.info(f"FlareSolverr session destroyed: {_fs_session_id}")
        except Exception as e:
            log.warn(f"Could not destroy FlareSolverr session: {e}")
        _fs_session_id = None
        _fs_cookies = {}
        _fs_user_agent = None


def _is_challenge_html(html: str, title: str = "") -> bool:
    content = (title + " " + html[:2000]).lower()
    return any(marker in content for marker in _CHALLENGE_MARKERS)


def _save_diagnostic_html(html: str, label: str) -> None:
    try:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        path = DIAGNOSTICS_DIR / f"{label}-{ts}.html"
        path.write_text(html, encoding="utf-8", errors="replace")
        log.info(f"Diagnostic HTML saved: {path}")
    except Exception as e:
        log.warn(f"Could not save diagnostic HTML: {e}")


async def _apply_global_cooldown():
    now = time.monotonic()
    if now < _cooldown_until:
        remaining = _cooldown_until - now
        log.wait(f"Global cooldown active, waiting {remaining:.0f}s...")
        await asyncio.sleep(remaining)


class _ChallengePresent(Exception):
    """Raised when FlareSolverr returned a still-challenged page."""

    def __init__(self, cooldown: float):
        super().__init__("Cloudflare challenge page detected")
        self.cooldown = cooldown


def _register_challenge() -> float:
    global _challenge_hits, _cooldown_until
    _challenge_hits += 1
    cooldown = min(15.0 * (2 ** (_challenge_hits - 1)), 120.0)
    _cooldown_until = time.monotonic() + cooldown
    return cooldown


class _ChallengeCooldownWait(wait_base):
    def __call__(self, retry_state) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        return exc.cooldown if isinstance(exc, _ChallengePresent) else 0.0


class _BudgetExceeded(stop_base):
    def __init__(self, deadline: float | None):
        self.deadline = deadline

    def __call__(self, retry_state) -> bool:
        if self.deadline is None or retry_state.outcome is None:
            return False
        exc = retry_state.outcome.exception()
        cooldown = exc.cooldown if isinstance(exc, _ChallengePresent) else 0.0
        return time.monotonic() + cooldown >= self.deadline


async def _fetch_page(
    url: str,
    *,
    wait: float = 1.2,
    challenge_retries: int = 3,
    label: str = "page",
    deadline: float | None = None,
) -> tuple[str, str]:
    """Fetch a URL through FlareSolverr. Returns (html, final_url)."""
    await _apply_global_cooldown()
    session_id = await _ensure_session()

    html = ""
    final_url = url

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(challenge_retries + 1) | _BudgetExceeded(deadline),
            wait=_ChallengeCooldownWait(),
            retry=retry_if_exception_type(_ChallengePresent),
            before_sleep=lambda rs: log.notice(
                f"Cloudflare challenge detected ({label}), cooling down "
                f"{(rs.next_action.sleep if rs.next_action else 0):.0f}s before retrying "
                f"(challenge #{_challenge_hits} this run)..."
            ),
            reraise=True,
        ):
            with attempt:
                log.browser(f"FlareSolverr GET [{label}]: {url}")
                data = await _fs_request(
                    "request.get",
                    url=url,
                    session=session_id,
                    maxTimeout=60000,
                )
                solution = data.get("solution") or {}
                html = solution.get("response") or ""
                final_url = solution.get("url") or url
                status = solution.get("status")

                # Keep clearance cookies + UA for later binary downloads
                global _fs_cookies, _fs_user_agent
                for c in solution.get("cookies") or []:
                    name = c.get("name")
                    if name:
                        _fs_cookies[name] = c.get("value", "")
                ua = solution.get("userAgent")
                if ua:
                    _fs_user_agent = ua

                title = ""
                try:
                    soup = BeautifulSoup(html, "lxml")
                    if soup.title and soup.title.string:
                        title = soup.title.string
                except Exception:
                    pass

                if _is_challenge_html(html, title) or (status and int(status) in (403, 503)):
                    raise _ChallengePresent(_register_challenge())

                await _jitter_sleep(wait)
    except _ChallengePresent:
        log.notice(f"Cloudflare challenge still present ({label}), proceeding anyway...")
        _save_diagnostic_html(html, f"cloudflare-{label}")

    return html, final_url


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _row_count(html: str) -> int:
    soup = _soup(html)
    return len(soup.select(".variants-table .table-row"))


def _is_404_page(html: str) -> bool:
    soup = _soup(html)
    title = (soup.title.string or "") if soup.title else ""
    body_text = soup.get_text(" ", strip=True)[:300]
    content = (title + " " + body_text).lower()
    return "404" in content and (
        "whoops" in content or "could not be found" in content or "not be found" in content
    )


def _has_download_button(html: str) -> bool:
    soup = _soup(html)
    return len(soup.select("a.downloadButton")) > 0


async def _page_exists(url: str, deadline: float | None = None) -> bool:
    try:
        html, _ = await _fetch_page(url, wait=1.0, label="direct-try", deadline=deadline)
        if _is_404_page(html):
            return False
        return _row_count(html) > 0
    except Exception:
        return False


async def _resolve_list_url(app_config: dict, version: str) -> tuple[str, bool, str | None]:
    """Return (list_or_variant_url, is_final, html_if_already_loaded)."""
    version_slug = to_apkmirror_version(version)
    name_part = app_config.get("release_slug") or app_config["slug"]
    folder_url = f"https://www.apkmirror.com/apk/{app_config['org']}/{app_config['slug']}"
    deadline = time.monotonic() + RESOLVE_BUDGET_SECONDS

    release_slugs = [
        f"{name_part}-{version_slug}-release",
        f"{name_part}-{version_slug}-release-0-release",
        f"{name_part}-{version_slug}-beta-0-release",
        f"{name_part}-{version_slug}-beta-1-release",
    ]

    for slug in release_slugs:
        if time.monotonic() > deadline:
            break

        candidate = f"{folder_url}/{slug}/"
        log.search(f"TRY: {candidate}")
        if await _page_exists(candidate, deadline=deadline):
            return candidate, False, None

        if time.monotonic() > deadline:
            break

        direct_variant = f"{candidate}{name_part}-{version_slug}-android-apk-download/"
        log.search(f"TRY (single-variant direct): {direct_variant}")
        try:
            html, _ = await _fetch_page(direct_variant, wait=1.0, label="direct-variant-try", deadline=deadline)
            if not _is_404_page(html) and _has_download_button(html):
                return direct_variant, True, html
        except Exception:
            pass

    if time.monotonic() > deadline:
        raise RuntimeError(
            f"Giving up on {app_config['slug']} v{version}: APKMirror kept challenge-walling every "
            f"attempt (exceeded {RESOLVE_BUDGET_SECONDS:.0f}s resolve budget)"
        )

    log.search("No direct match, scanning app listing page...")
    listing_url = f"{folder_url}/"
    slug_part = f"-{version_slug}-"

    for attempt in range(2):
        if time.monotonic() > deadline:
            break
        html, _ = await _fetch_page(listing_url, wait=1.5 + attempt, label="listing-scan", deadline=deadline)
        soup = _soup(html)
        for a in soup.select("a[href*='-release/']"):
            href = a.get("href") or ""
            if slug_part in href and "#" not in href:
                found = urljoin("https://www.apkmirror.com", href)
                return found, False, None

    raise RuntimeError(f"No APKMirror release page found for version {version}")


def _dump_variant_rows_for_debug(html: str) -> None:
    soup = _soup(html)
    rows = soup.select(".table-row")
    scoped = soup.select(".variants-table .table-row")
    is404 = _is_404_page(html)
    log.info(
        f"Debug: page has {len(rows)} .table-row elements "
        f"({len(scoped)} of them inside the real .variants-table), is404: {is404}"
    )
    for i, row in enumerate(rows[:20]):
        cells = row.select(".table-cell")
        name = cells[0].get_text(strip=True)[:60] if cells else None
        arch = cells[1].get_text(strip=True) if len(cells) > 1 else None
        dpi = cells[3].get_text(strip=True) if len(cells) > 3 else None
        log.info(f"   [{i}] cells={len(cells)} name={name!r} arch={arch!r} dpi={dpi!r}")


def _extract_variant_url(html: str, force_build: str | None, app_name: str) -> str | None:
    soup = _soup(html)
    rows = soup.select(".variants-table .table-row")
    candidates: list[str | None] = [None, None, None, None, None, None]
    allowed_archs = [
        "universal",
        "evrensel",
        "noarch",
        "arm64-v8a",
        "arm64-v8a + armeabi-v7a",
        "arm64-v8a + armeabi",
    ]

    for row in rows:
        cells = row.select(".table-cell")
        if len(cells) < 4:
            continue

        link = cells[0].select_one("a.accent_color")
        if not link:
            continue
        href = link.get("href")
        if not href:
            continue

        if force_build and force_build not in cells[0].get_text():
            continue

        badge = cells[0].select_one(".apkm-badge")
        badge_text = (badge.get_text() if badge else "").upper()
        is_bundle = "BUNDLE" in badge_text or "PAKET" in badge_text

        if app_name == "instagram" and not is_bundle:
            continue

        arch_text = cells[1].get_text(strip=True).lower()
        dpi_text = cells[3].get_text(strip=True).lower()

        is_target_arch = arch_text == "" or any(a in arch_text for a in allowed_archs)
        if not is_target_arch:
            continue

        is_nodpi = dpi_text == "" or "nodpi" in dpi_text
        is_anydpi = "anydpi" in dpi_text

        if is_nodpi:
            slot = 3 if is_bundle else 0
        elif is_anydpi:
            slot = 4 if is_bundle else 1
        else:
            slot = 5 if is_bundle else 2

        if not candidates[slot]:
            candidates[slot] = urljoin("https://www.apkmirror.com", href)

    return next((c for c in candidates if c), None)


async def _download_binary(url: str, out_path: Path) -> None:
    """Download a binary file using the clearance cookies + UA from FlareSolverr."""
    headers: dict[str, str] = {}
    if _fs_user_agent:
        headers["User-Agent"] = _fs_user_agent
    headers["Referer"] = "https://www.apkmirror.com/"

    async with new_session(timeout=300, follow_redirects=True) as session:
        resp = await session.get(url, cookies=_fs_cookies, headers=headers)
        resp.raise_for_status()
        content = resp.content
        if len(content) < 1024:
            raise RuntimeError(f"Downloaded file too small ({len(content)} bytes)")
        out_path.write_bytes(content)


def _find_download_href(html: str, base_url: str) -> str | None:
    """Extract the best download link from a variant or confirm page."""
    soup = _soup(html)

    # Prefer the final #download-link if present (confirm interstitial)
    final = soup.select_one("#download-link")
    if final and final.get("href"):
        href = final["href"]
        if not href.startswith(("javascript:", "#")):
            return urljoin(base_url, href)

    # Main download button
    btn = soup.select_one("a.downloadButton")
    if btn and btn.get("href"):
        href = btn["href"]
        if not href.startswith(("javascript:", "#")):
            return urljoin(base_url, href)

    return None


async def _attempt_download_from_html(html: str, page_url: str, out_dir: Path) -> Path | None:
    """
    From a variant/confirm page HTML, resolve the real download URL and fetch the file.
    May need a second FlareSolverr round-trip if the first link is only a confirm page.
    """
    href = _find_download_href(html, page_url)
    if not href:
        return None

    log.browser(f"Download candidate: {href}")

    # If the href still points at apkmirror.com (confirm page), fetch it first
    if "apkmirror.com" in href and not href.rstrip("/").endswith((".apk", ".apkm", ".xapk")):
        confirm_html, confirm_url = await _fetch_page(href, wait=1.5, label="confirm-page")
        final_href = _find_download_href(confirm_html, confirm_url)
        if not final_href:
            # Sometimes the confirm page itself already has the direct CDN link in the same button
            final_href = href
        href = final_href
        log.browser(f"Resolved download URL: {href}")

    # Guess filename from URL
    name = href.split("?")[0].rstrip("/").split("/")[-1] or "download.apk"
    if not name.lower().endswith((".apk", ".apkm", ".xapk", ".zip")):
        name = name + ".apk"
    out_path = out_dir / name

    # Prefer raw HTTP with clearance cookies (much faster than routing binary through FlareSolverr)
    try:
        await _download_binary(href, out_path)
        return out_path
    except Exception as e:
        log.notice(f"Cookie download failed ({e}), retrying via FlareSolverr...")

    # Fallback: let FlareSolverr fetch the URL (it will follow redirects; response is HTML or binary base64 in some setups —
    # for pure binary we still rely on cookies; if this also fails we raise)
    data = await _fs_request(
        "request.get",
        url=href,
        session=await _ensure_session(),
        maxTimeout=120000,
    )
    solution = data.get("solution") or {}
    # Update cookies again
    global _fs_cookies, _fs_user_agent
    for c in solution.get("cookies") or []:
        n = c.get("name")
        if n:
            _fs_cookies[n] = c.get("value", "")
    if solution.get("userAgent"):
        _fs_user_agent = solution["userAgent"]

    # Try cookie download one more time with refreshed cookies
    await _download_binary(href, out_path)
    return out_path


async def download_apk(version: str, app_name: str = "youtube", force_build: str | None = None) -> str:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    out_dir = Path(__file__).resolve().parent.parent.parent / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        list_url, is_final, cached_html = await _resolve_list_url(app_config, version)
        log.info(f"LIST: {list_url}")

        if is_final:
            variant_url = list_url
            html = cached_html or (await _fetch_page(variant_url, wait=1.2, label="variant-page"))[0]
            log.info(f"VARIANT: {variant_url} (single-variant release)")
        else:
            variant_url = None
            html = ""
            for attempt in range(4):
                html, _ = await _fetch_page(list_url, wait=1.5 + attempt * 1.0, label="list-page")
                variant_url = _extract_variant_url(html, force_build, app_name)
                if variant_url:
                    break
                log.notice(f"No matching row found on page, retrying ({attempt + 1}/4)...")

            if not variant_url:
                _dump_variant_rows_for_debug(html)
                _save_diagnostic_html(html, f"no-variant-{app_name}")
                raise RuntimeError("No matching variant found on APKMirror")

            log.info(f"VARIANT: {variant_url}")
            html, _ = await _fetch_page(variant_url, wait=1.2, label="variant-page")

        log.browser("Resolving download link...")
        assert variant_url is not None
        final_path: Path | None = None

        try:
            async for retry_attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=_ChallengeCooldownWait(),
                retry=retry_if_exception_type(_ChallengePresent),
                before_sleep=lambda rs: log.notice(
                    f"Download resolution failed, cooling down "
                    f"{(rs.next_action.sleep if rs.next_action else 0):.0f}s before retrying "
                    f"(attempt #{_challenge_hits} this run)..."
                ),
                reraise=True,
            ):
                with retry_attempt:
                    if retry_attempt.retry_state.attempt_number > 1:
                        html, _ = await _fetch_page(variant_url, wait=1.2, label="variant-page-retry")
                        log.browser("Resolving download link...")
                    final_path = await _attempt_download_from_html(html, variant_url, out_dir)
                    if final_path is None:
                        raise _ChallengePresent(_register_challenge())
        except _ChallengePresent:
            pass

        if final_path is None or not final_path.exists():
            log.error("Download did not start / file not detected.")
            _save_diagnostic_html(html, f"no-download-{app_name}")
            raise RuntimeError("Download did not start / file not detected.")

        size = final_path.stat().st_size
        if size < 1024:
            raise RuntimeError(f"Downloaded file too small ({size} bytes)")

        log.success(f"DONE: {final_path} ({size / 1024 / 1024:.2f} MB)")
        return str(final_path)

    except Exception:
        raise


def _version_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r"-(\d[\d]*(?:-\d+)+)-release", href)
    if not match:
        return None
    return match.group(1).replace("-", ".")


async def get_latest_listing(app_name: str) -> dict | None:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    try:
        listing_url = f"https://www.apkmirror.com/apk/{app_config['org']}/{app_config['slug']}/"
        log.info(f"LISTING: {listing_url}")

        candidates: list[Any] = []
        for attempt in range(4):
            html, _ = await _fetch_page(listing_url, wait=2.5 + attempt * 1.2, label="app-listing")
            soup = _soup(html)
            candidates = []
            for link in soup.select("a[href*='-release/']")[:15]:
                href = link.get("href")
                if href:
                    href = urljoin("https://www.apkmirror.com", href)
                row = link.find_parent(["div", "li", "tr"]) or link.parent
                text = row.get_text(" ", strip=True) if row else link.get_text(strip=True)
                candidates.append({"href": href, "text": text or ""})
            if candidates:
                break
            log.notice(f"No link found on listing page, retrying ({attempt + 1}/4)...")

        if not candidates:
            _save_diagnostic_html("", f"no-listing-{app_name}")
            return None

        for item in candidates:
            href = item.get("href") if isinstance(item, dict) else None
            text = item.get("text", "") if isinstance(item, dict) else ""

            version = _version_from_href(href)
            if not version:
                match = re.search(r"\d+(?:\.\d+)+", text)
                version = match.group(0) if match else None

            if version:
                return {"version": version, "href": href}

        return None

    except Exception:
        raise
