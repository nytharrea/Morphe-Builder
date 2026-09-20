import asyncio
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import lxml.html
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from .. import log
from ..apk.versions import to_apkmirror_version
from . import flaresolverr
from .flaresolverr import Cleared, FlareSolverrError

APP_SITES = {
    "youtube": {"org": "google-inc", "slug": "youtube"},
    "youtube-music": {"org": "google-inc", "slug": "youtube-music"},
    "reddit": {"org": "reddit-inc", "slug": "reddit"},
    "twitter": {"org": "x-corp", "slug": "twitter", "release_slug": "x"},
    "instagram": {"org": "instagram", "slug": "instagram"},
    "gboard": {"org": "google-inc", "slug": "gboard", "release_slug": "gboard-the-google-keyboard"},
    "speedtest": {"org": "ookla", "slug": "speedtest", "release_slug": "speedtest-by-ookla"},
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
    "fairemail": {
        "org": "marcel-bokhorst",
        "slug": "fairemail-open-source-privacy-oriented-email",
        "release_slug": "fairemail-privacy-aware-email",
    },
}

DIAGNOSTICS_DIR = Path(__file__).resolve().parent.parent.parent / "diagnostics"

RESOLVE_BUDGET_SECONDS = 300.0

_ALLOWED_ARCHS = [
    "universal",
    "evrensel",
    "noarch",
    "arm64-v8a",
    "arm64-v8a + armeabi-v7a",
    "arm64-v8a + armeabi",
]

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

_challenge_hits = 0
_cooldown_until = 0.0


async def close_session() -> None:
    await flaresolverr.close_session()


async def _apply_global_cooldown() -> None:
    now = time.monotonic()
    if now < _cooldown_until:
        remaining = _cooldown_until - now
        log.wait(f"Global cooldown active, waiting {remaining:.0f}s...")
        await asyncio.sleep(remaining)


class _ChallengePresent(Exception):
    """Raised internally when a fetched page is still a Cloudflare
    challenge (or FlareSolverr couldn't clear it). Carries the escalated
    cooldown so the wait/stop strategies below don't have to recompute
    (and re-escalate) it themselves."""

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


def _parse(html: str) -> lxml.html.HtmlElement | None:
    if not html:
        return None
    try:
        return lxml.html.fromstring(html)
    except Exception:
        return None


def _abs_url(base: str, href: str | None) -> str | None:
    return urljoin(base, href) if href else None


def _page_text(tree: lxml.html.HtmlElement | None, limit: int) -> str:
    if tree is None:
        return ""
    title = tree.findtext(".//title") or ""
    body = tree.find(".//body")
    body_text = " ".join(body.itertext())[:limit] if body is not None else ""
    return f"{title} {body_text}".lower()


def _looks_like_challenge(html: str) -> bool:
    if not html:
        return False
    content = _page_text(_parse(html), 500) or html[:1000].lower()
    return any(marker in content for marker in _CHALLENGE_MARKERS)


def _is_404_html(tree: lxml.html.HtmlElement | None) -> bool:
    content = _page_text(tree, 300)
    if "404" not in content:
        return False
    return "whoops" in content or "could not be found" in content or "not be found" in content


async def _save_diagnostic_html(html: str, label: str) -> None:
    try:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        path = DIAGNOSTICS_DIR / f"{label}-{int(time.time())}.html"
        path.write_text(html or "", encoding="utf-8", errors="replace")
        log.info(f"Diagnostic HTML saved: {path}")
    except Exception as e:
        log.warn(f"Could not save diagnostic HTML: {e}")


async def _fetch(url: str, label: str, deadline: float | None = None, challenge_retries: int = 3) -> Cleared:
    await _apply_global_cooldown()
    cleared: Cleared | None = None

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
                log.browser(f"Requesting via FlareSolverr ({label}): {url}")
                try:
                    cleared = await flaresolverr.get(url)
                except FlareSolverrError as e:
                    raise RuntimeError(f"FlareSolverr could not fetch {url}: {e}") from e
                if cleared.status >= 400 or _looks_like_challenge(cleared.html):
                    raise _ChallengePresent(_register_challenge())
    except _ChallengePresent:
        log.notice(f"Cloudflare challenge still present ({label}), proceeding anyway...")
        if cleared is not None:
            await _save_diagnostic_html(cleared.html, f"cloudflare-{label}")

    if cleared is None:
        raise RuntimeError(f"Could not fetch {url} ({label})")
    return cleared


def _classes(el) -> list[str]:
    return list(getattr(el, "classes", None) or [])


def _cell_text(cell) -> str:
    return (cell.text_content() or "").strip() if cell is not None else ""


def _closest(el, tags: set[str]):
    node = el
    while node is not None:
        if node.tag in tags:
            return node
        node = node.getparent()
    return None


def _variant_rows(tree: lxml.html.HtmlElement | None) -> list:
    if tree is None:
        return []
    rows = []
    for el in tree.iter():
        if "table-row" not in _classes(el):
            continue
        ancestor = el.getparent()
        while ancestor is not None:
            if "variants-table" in _classes(ancestor):
                rows.append(el)
                break
            ancestor = ancestor.getparent()
    return rows


def _row_count(tree: lxml.html.HtmlElement | None) -> int:
    return len(_variant_rows(tree))


def _has_download_button(tree: lxml.html.HtmlElement | None) -> bool:
    if tree is None:
        return False
    return any("downloadButton" in _classes(a) for a in tree.iter("a"))


def _extract_variant_url(tree: lxml.html.HtmlElement | None, force_build: str | None, app_name: str) -> str | None:
    candidates: list[str | None] = [None] * 6

    for row in _variant_rows(tree):
        cells = [c for c in row.iterchildren() if "table-cell" in _classes(c)]
        if len(cells) < 4:
            continue

        link = next((a for a in cells[0].iter("a") if "accent_color" in _classes(a)), None)
        if link is None:
            continue

        if force_build and force_build not in _cell_text(cells[0]):
            continue

        badge = next((b for b in cells[0].iter() if "apkm-badge" in _classes(b)), None)
        badge_text = _cell_text(badge).upper()
        is_bundle = "BUNDLE" in badge_text or "PAKET" in badge_text

        if app_name == "instagram" and not is_bundle:
            continue

        arch_text = _cell_text(cells[1]).lower()
        dpi_text = _cell_text(cells[3]).lower()

        is_target_arch = arch_text == "" or any(a in arch_text for a in _ALLOWED_ARCHS)
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

        if candidates[slot] is None:
            candidates[slot] = link.get("href")

    return next((c for c in candidates if c), None)


def _dump_variant_rows_for_debug(tree: lxml.html.HtmlElement | None) -> None:
    all_rows = [el for el in (tree.iter() if tree is not None else []) if "table-row" in _classes(el)]
    scoped_rows = _variant_rows(tree)

    log.info(
        f"Debug: page has {len(all_rows)} .table-row elements "
        f"({len(scoped_rows)} of them inside the real .variants-table), is404: {_is_404_html(tree)}"
    )
    for i, row in enumerate(scoped_rows[:20]):
        cells = [c for c in row.iterchildren() if "table-cell" in _classes(c)]
        name = _cell_text(cells[0])[:60] if len(cells) > 0 else None
        arch = _cell_text(cells[1]) if len(cells) > 1 else None
        dpi = _cell_text(cells[3]) if len(cells) > 3 else None
        log.info(f"   [{i}] cells={len(cells)} name={name!r} arch={arch!r} dpi={dpi!r}")


async def _page_exists(url: str, deadline: float | None = None) -> bool:
    try:
        cleared = await _fetch(url, label="direct-try", deadline=deadline)
        tree = _parse(cleared.html)
        return not _is_404_html(tree) and _row_count(tree) > 0
    except Exception:
        return False


def _find_listing_link(tree: lxml.html.HtmlElement, base_url: str, slug_part: str) -> str | None:
    for a in tree.iter("a"):
        href = a.get("href")
        if href and "-release/" in href and slug_part in href and "#" not in href:
            return _abs_url(base_url, href)
    return None


async def _resolve_list_url(app_config: dict, version: str) -> tuple[str, bool]:
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
            return candidate, False

        if time.monotonic() > deadline:
            break

        direct_variant = f"{candidate}{name_part}-{version_slug}-android-apk-download/"
        log.search(f"TRY (single-variant direct): {direct_variant}")
        try:
            cleared = await _fetch(direct_variant, label="direct-variant-try", deadline=deadline)
            tree = _parse(cleared.html)
            if not _is_404_html(tree) and _has_download_button(tree):
                return direct_variant, True
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
    last_cleared: Cleared | None = None

    for _attempt in range(2):
        if time.monotonic() > deadline:
            break
        last_cleared = await _fetch(listing_url, label="listing-scan", deadline=deadline)
        tree = _parse(last_cleared.html)
        found_url = _find_listing_link(tree, listing_url, slug_part) if tree is not None else None
        if found_url:
            return found_url, False

    if last_cleared is not None:
        await _save_diagnostic_html(last_cleared.html, f"no-match-{app_config['slug']}")
    raise RuntimeError(f"No APKMirror release page found for version {version}")


async def _resolve_download_url(variant_url: str, variant_cleared: Cleared) -> tuple[str, Cleared]:
    tree = _parse(variant_cleared.html)
    buttons = tree.iter("a") if tree is not None else []
    button = next((a for a in buttons if "downloadButton" in _classes(a)), None)
    if button is None or not button.get("href"):
        raise _ChallengePresent(_register_challenge())

    confirm_url = _abs_url(variant_url, button.get("href"))
    assert confirm_url is not None
    confirm_cleared = await _fetch(confirm_url, label="confirm-page")
    confirm_tree = _parse(confirm_cleared.html)

    link = confirm_tree.get_element_by_id("download-link", None) if confirm_tree is not None else None
    file_url = _abs_url(confirm_url, link.get("href")) if link is not None else None
    if file_url:
        return file_url, confirm_cleared

    return confirm_url, confirm_cleared


async def download_apk(version: str, app_name: str = "youtube", force_build: str | None = None) -> str:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    out_dir = Path(__file__).resolve().parent.parent.parent / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)

    list_url, is_final = await _resolve_list_url(app_config, version)
    log.info(f"LIST: {list_url}")

    if is_final:
        variant_url = list_url
        log.info(f"VARIANT: {variant_url} (single-variant release)")
    else:
        listing_base = f"https://www.apkmirror.com/apk/{app_config['org']}/{app_config['slug']}/"
        variant_url = None
        for attempt in range(4):
            cleared = await _fetch(list_url, label="list-page")
            tree = _parse(cleared.html)
            found = _extract_variant_url(tree, force_build, app_name) if tree is not None else None
            variant_url = _abs_url(listing_base, found) if found else None
            if variant_url:
                break
            log.notice(f"No matching row found on page, retrying ({attempt + 1}/4)...")
            _dump_variant_rows_for_debug(tree)

        if not variant_url:
            await _save_diagnostic_html(cleared.html, f"no-variant-{app_name}")
            raise RuntimeError("No matching variant found on APKMirror")
        log.info(f"VARIANT: {variant_url}")

    assert variant_url is not None

    final_path: Path | None = None
    last_error: Exception | None = None
    last_variant_cleared: Cleared | None = None

    try:
        async for retry_attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=_ChallengeCooldownWait(),
            retry=retry_if_exception_type(_ChallengePresent),
            before_sleep=lambda rs: log.notice(
                f"Download attempt had no effect, cooling down "
                f"{(rs.next_action.sleep if rs.next_action else 0):.0f}s before retrying "
                f"(attempt #{_challenge_hits} this run)..."
            ),
            reraise=True,
        ):
            with retry_attempt:
                variant_cleared = await _fetch(variant_url, label="variant-page")
                last_variant_cleared = variant_cleared
                file_url, cleared_for_cookies = await _resolve_download_url(variant_url, variant_cleared)

                log.download(f"Downloading: {file_url}")
                try:
                    candidate_path = await flaresolverr.download_file(
                        file_url, cleared_for_cookies, out_dir, f"{app_name}.apk"
                    )
                except FlareSolverrError as e:
                    last_error = e
                    raise _ChallengePresent(_register_challenge()) from e

                size = candidate_path.stat().st_size if candidate_path.exists() else 0
                if size < 1024:
                    candidate_path.unlink(missing_ok=True)
                    last_error = RuntimeError(f"Downloaded file too small ({size} bytes)")
                    raise _ChallengePresent(_register_challenge())

                final_path = candidate_path
    except _ChallengePresent:
        pass

    if final_path is None:
        if last_variant_cleared is not None:
            await _save_diagnostic_html(last_variant_cleared.html, f"no-download-{app_name}")
        raise last_error or RuntimeError("Download did not start / file not detected.")

    log.success(f"DONE: {final_path} ({final_path.stat().st_size / 1024 / 1024:.2f} MB)")
    return str(final_path)


def _version_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r"-(\d[\d]*(?:-\d+)+)-release", href)
    if not match:
        return None
    return match.group(1).replace("-", ".")


def _listing_candidates(tree: lxml.html.HtmlElement, base_url: str) -> list[tuple[str, str]]:
    results = []
    for a in tree.iter("a"):
        href = a.get("href")
        if not href or "-release/" not in href:
            continue
        row = _closest(a, {"div", "li", "tr"})
        if row is None:
            row = a.getparent() if a.getparent() is not None else a
        abs_href = _abs_url(base_url, href)
        if abs_href:
            results.append((abs_href, row.text_content() or ""))
        if len(results) >= 15:
            break
    return results


async def get_latest_listing(app_name: str) -> dict | None:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    listing_url = f"https://www.apkmirror.com/apk/{app_config['org']}/{app_config['slug']}/"
    log.info(f"LISTING: {listing_url}")

    cleared: Cleared | None = None
    candidates: list[tuple[str, str]] = []
    for attempt in range(4):
        cleared = await _fetch(listing_url, label="app-listing")
        tree = _parse(cleared.html)
        candidates = _listing_candidates(tree, listing_url) if tree is not None else []
        if candidates:
            break
        log.notice(f"No link found on listing page, retrying ({attempt + 1}/4)...")

    if not candidates:
        if cleared is not None:
            await _save_diagnostic_html(cleared.html, f"no-listing-{app_name}")
        return None

    for href, text in candidates:
        version = _version_from_href(href)
        if not version:
            match = re.search(r"\d+(?:\.\d+)+", text)
            version = match.group(0) if match else None
        if version:
            return {"version": version, "href": href}

    if cleared is not None:
        await _save_diagnostic_html(cleared.html, f"no-version-{app_name}")
    return None
