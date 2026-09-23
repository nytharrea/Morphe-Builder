"""Downloads apps from apkmirror.com: resolves an app+version to the right
release page, works through APKMirror's variant-row -> download-button ->
confirm-page -> final-file hop, and streams the file to disk - retrying
through Cloudflare challenges (apkmirror_challenge.py) as it goes and using
apkmirror_parse.py to make sense of each page's HTML along the way.

Public API: close_session, download_apk, get_latest_listing, ApkMirrorSite.
Everything else here is orchestration private to this module.
"""

import re
import time
from pathlib import Path
from typing import NotRequired, TypedDict

from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

from .. import log, paths
from ..apk.versions import to_apkmirror_version
from . import flaresolverr
from .apkmirror_challenge import (
    BudgetExceeded as _BudgetExceeded,
)
from .apkmirror_challenge import (
    ChallengeCooldownWait as _ChallengeCooldownWait,
)
from .apkmirror_challenge import (
    ChallengePresent as _ChallengePresent,
)
from .apkmirror_challenge import apply_global_cooldown as _apply_global_cooldown
from .apkmirror_challenge import challenge_hits
from .apkmirror_challenge import looks_like_challenge as _looks_like_challenge_impl
from .apkmirror_challenge import register_challenge as _register_challenge
from .apkmirror_parse import abs_url as _abs_url
from .apkmirror_parse import classes as _classes
from .apkmirror_parse import closest as _closest  # noqa: F401 - re-exported, test_apkmirror.py imports it directly
from .apkmirror_parse import dump_variant_rows_for_debug as _dump_variant_rows_for_debug
from .apkmirror_parse import extract_variant_url as _extract_variant_url
from .apkmirror_parse import find_listing_link as _find_listing_link
from .apkmirror_parse import has_download_button as _has_download_button
from .apkmirror_parse import is_404_html as _is_404_html
from .apkmirror_parse import listing_candidates as _listing_candidates
from .apkmirror_parse import page_text as _page_text
from .apkmirror_parse import parse as _parse
from .apkmirror_parse import row_count as _row_count
from .apkmirror_parse import (
    variant_rows as _variant_rows,  # noqa: F401 - re-exported, test_apkmirror.py imports it directly
)
from .apkmirror_parse import version_from_href as _version_from_href
from .flaresolverr import Cleared, FlareSolverrError


class ApkMirrorSite(TypedDict):
    """The org/slug/release_slug an app's `apk_source` carries in
    catalog/apps.yaml (type: apkmirror) - this module doesn't read the
    catalog itself, so every function below takes one of these as a plain
    parameter instead of looking an app name up in a hardcoded table."""

    org: str
    slug: str
    release_slug: NotRequired[str]


RESOLVE_BUDGET_SECONDS = 300.0


def _looks_like_challenge(html: str) -> bool:
    """apkmirror_challenge.looks_like_challenge() takes a page-text
    function as a parameter so that module never has to import this one;
    this is that function, supplied here where both pieces are in scope."""
    return _looks_like_challenge_impl(html, lambda h: _page_text(_parse(h), 500))


async def close_session() -> None:
    await flaresolverr.close_session()


async def _save_diagnostic_html(html: str, label: str) -> None:
    try:
        diagnostics_dir = paths.diagnostics_dir()
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        path = diagnostics_dir / f"{label}-{int(time.time())}.html"
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
                f"(challenge #{challenge_hits()} this run)..."
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


async def _page_exists(url: str, deadline: float | None = None) -> bool:
    try:
        cleared = await _fetch(url, label="direct-try", deadline=deadline)
        tree = _parse(cleared.html)
        return not _is_404_html(tree) and _row_count(tree) > 0
    except Exception:
        return False


async def _resolve_list_url(site: ApkMirrorSite, version: str) -> tuple[str, bool]:
    version_slug = to_apkmirror_version(version)
    name_part = site.get("release_slug") or site["slug"]
    folder_url = f"https://www.apkmirror.com/apk/{site['org']}/{site['slug']}"
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
            f"Giving up on {site['slug']} v{version}: APKMirror kept challenge-walling every "
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
        await _save_diagnostic_html(last_cleared.html, f"no-match-{site['slug']}")
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


async def download_apk(version: str, app_slug: str, site: ApkMirrorSite, force_build: str | None = None) -> str:
    out_dir = paths.downloads_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    list_url, is_final = await _resolve_list_url(site, version)
    log.info(f"LIST: {list_url}")

    if is_final:
        variant_url = list_url
        log.info(f"VARIANT: {variant_url} (single-variant release)")
    else:
        listing_base = f"https://www.apkmirror.com/apk/{site['org']}/{site['slug']}/"
        variant_url = None
        for attempt in range(4):
            cleared = await _fetch(list_url, label="list-page")
            tree = _parse(cleared.html)
            found = _extract_variant_url(tree, force_build, app_slug) if tree is not None else None
            variant_url = _abs_url(listing_base, found) if found else None
            if variant_url:
                break
            log.notice(f"No matching row found on page, retrying ({attempt + 1}/4)...")
            _dump_variant_rows_for_debug(tree)

        if not variant_url:
            await _save_diagnostic_html(cleared.html, f"no-variant-{app_slug}")
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
                f"(attempt #{challenge_hits()} this run)..."
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
                        file_url, cleared_for_cookies, out_dir, f"{app_slug}.apk"
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
            await _save_diagnostic_html(last_variant_cleared.html, f"no-download-{app_slug}")
        raise last_error or RuntimeError("Download did not start / file not detected.")

    log.success(f"DONE: {final_path} ({final_path.stat().st_size / 1024 / 1024:.2f} MB)")
    return str(final_path)


async def get_latest_listing(app_slug: str, site: ApkMirrorSite) -> dict | None:
    listing_url = f"https://www.apkmirror.com/apk/{site['org']}/{site['slug']}/"
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
            await _save_diagnostic_html(cleared.html, f"no-listing-{app_slug}")
        return None

    for href, text in candidates:
        version = _version_from_href(href)
        if not version:
            match = re.search(r"\d+(?:\.\d+)+", text)
            version = match.group(0) if match else None
        if version:
            return {"version": version, "href": href}

    if cleared is not None:
        await _save_diagnostic_html(cleared.html, f"no-version-{app_slug}")
    return None
