import asyncio
import base64
import contextlib
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from tenacity import AsyncRetrying, retry, retry_if_exception_type, stop_after_attempt
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from .. import flaresolverr, log
from .. import retry as retry_conf
from ..apk.versions import to_apkmirror_version
from ..http import new_session
from . import apkmirror_html as parser

# Explicit value type so mypy treats APP_SITES.get() as dict | None, not object | None.
AppSiteConfig = dict[str, str | int | None]
APP_SITES: dict[str, AppSiteConfig] = {
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
        "tabs_till_verify": None,
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

FLARESOLVERR_MAX_TIMEOUT_MS = 60_000
FLARESOLVERR_CONFIRM_TIMEOUT_MS = 90_000

_flaresolverr_session: str | None = None
_challenge_hits = 0
_cooldown_until = 0.0


class _ChallengePresent(Exception):
    def __init__(self, cooldown: float):
        super().__init__("Cloudflare challenge page detected")
        self.cooldown = cooldown


class _DownloadNotAFile(Exception):
    pass


def _register_challenge() -> float:
    global _challenge_hits, _cooldown_until
    _challenge_hits += 1
    cooldown = min(15.0 * (2 ** (_challenge_hits - 1)), 120.0)
    _cooldown_until = time.monotonic() + cooldown
    return cooldown


async def _apply_global_cooldown() -> None:
    now = time.monotonic()
    if now < _cooldown_until:
        remaining = _cooldown_until - now
        log.wait(f"Global cooldown active, waiting {remaining:.0f}s...")
        await asyncio.sleep(remaining)


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


@retry(
    stop=stop_after_attempt(6),
    wait=retry_conf.incrementing(start=1.5, increment=1.5, max=8.0),
    before_sleep=retry_conf.before_sleep("Could not create FlareSolverr session"),
    reraise=True,
)
async def _start_session() -> str:
    log.info("Creating FlareSolverr session...")
    return await flaresolverr.create_session()


async def _get_session() -> str:
    global _flaresolverr_session
    if _flaresolverr_session is None:
        _flaresolverr_session = await _start_session()
    return _flaresolverr_session


async def close_session() -> None:
    global _flaresolverr_session
    if _flaresolverr_session is not None:
        with contextlib.suppress(Exception):
            await flaresolverr.destroy_session(_flaresolverr_session)
        _flaresolverr_session = None


async def _save_diagnostics(session: str, url: str, label: str) -> None:
    try:
        solution = await flaresolverr.solve_get(
            url, session=session, max_timeout_ms=FLARESOLVERR_MAX_TIMEOUT_MS, return_screenshot=True
        )
    except flaresolverr.FlareSolverrError as e:
        log.warn(f"Could not capture diagnostics: {e}")
        return

    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    html_path = DIAGNOSTICS_DIR / f"{label}-{ts}.html"
    try:
        html_path.write_text(solution.html, encoding="utf-8")
        log.info(f"Diagnostic HTML saved: {html_path}")
    except Exception as e:
        log.warn(f"Could not save diagnostic HTML: {e}")

    if solution.screenshot_base64:
        png_path = DIAGNOSTICS_DIR / f"{label}-{ts}.png"
        try:
            png_path.write_bytes(base64.b64decode(solution.screenshot_base64))
            log.info(f"Diagnostic screenshot saved: {png_path}")
        except Exception as e:
            log.warn(f"Could not save diagnostic screenshot: {e}")


async def _fetch(
    session: str,
    url: str,
    wait_seconds: float = 1.2,
    challenge_retries: int = 3,
    label: str = "page",
    deadline: float | None = None,
    max_timeout_ms: int = FLARESOLVERR_MAX_TIMEOUT_MS,
    tabs_till_verify: int | None = None,
) -> flaresolverr.Solution:
    await _apply_global_cooldown()

    solution: flaresolverr.Solution | None = None

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
                try:
                    solution = await flaresolverr.solve_get(
                        url,
                        session=session,
                        max_timeout_ms=max_timeout_ms,
                        wait_seconds=wait_seconds,
                        tabs_till_verify=tabs_till_verify,
                    )
                except flaresolverr.FlareSolverrError as e:
                    raise _ChallengePresent(_register_challenge()) from e
                if parser.is_challenge_page(solution.html):
                    raise _ChallengePresent(_register_challenge())
    except _ChallengePresent:
        if solution is None:
            raise RuntimeError(f"FlareSolverr could not resolve {label}: {url}") from None
        log.notice(f"Cloudflare challenge still present ({label}), proceeding anyway...")
        await _save_diagnostics(session, url, f"cloudflare-{label}")

    assert solution is not None
    return solution


async def _page_exists(session: str, url: str, deadline: float | None = None) -> bool:
    try:
        solution = await _fetch(session, url, wait_seconds=1.0, label="direct-try", deadline=deadline)
        if parser.is_404_page(solution.html):
            return False
        return parser.row_count(solution.html) > 0
    except Exception:
        return False


async def _resolve_list_url(session: str, app_config: dict, version: str) -> tuple[str, bool]:
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
        if await _page_exists(session, candidate, deadline=deadline):
            return candidate, False

        if time.monotonic() > deadline:
            break

        direct_variant = f"{candidate}{name_part}-{version_slug}-android-apk-download/"
        log.search(f"TRY (single-variant direct): {direct_variant}")
        try:
            solution = await _fetch(
                session, direct_variant, wait_seconds=1.0, label="direct-variant-try", deadline=deadline
            )
            if not parser.is_404_page(solution.html) and parser.has_download_button(solution.html):
                return direct_variant, True
        except Exception:
            pass

    if time.monotonic() > deadline:
        await _save_diagnostics(session, folder_url + "/", f"budget-exceeded-{app_config['slug']}")
        raise RuntimeError(
            f"Giving up on {app_config['slug']} v{version}: APKMirror kept challenge-walling every "
            f"attempt (exceeded {RESOLVE_BUDGET_SECONDS:.0f}s resolve budget)"
        )

    log.search("No direct match, scanning app listing page...")
    listing_url = f"{folder_url}/"

    for attempt in range(2):
        if time.monotonic() > deadline:
            break
        solution = await _fetch(
            session, listing_url, wait_seconds=1.5 + attempt, label="listing-scan", deadline=deadline
        )
        found_url = parser.find_release_link(solution.html, version_slug)
        if found_url:
            return found_url, False

    await _save_diagnostics(session, listing_url, f"no-match-{app_config['slug']}")
    raise RuntimeError(f"No APKMirror release page found for version {version}")


async def _download_file(url: str, cookies: dict[str, str], user_agent: str, out_dir: Path) -> Path:
    async with (
        new_session(impersonate="chrome", timeout=180) as client,
        client.stream("GET", url, cookies=cookies, headers={"User-Agent": user_agent}) as response,
    ):
        content_type = (response.headers.get("content-type") or "").lower()
        if response.status_code >= 400 or "text/html" in content_type:
            raise _DownloadNotAFile(f"HTTP {response.status_code}, content-type {content_type!r}")

        filename = parser.filename_from_response(url, response.headers)
        final_path = out_dir / filename
        with open(final_path, "wb") as f:
            async for chunk in response.aiter_content():
                f.write(chunk)

    size = final_path.stat().st_size
    if size < 1024:
        raise RuntimeError(f"Downloaded file too small ({size} bytes)")

    # If the CDN left us with a generic name, sniff ZIP contents for a better extension.
    if final_path.suffix.lower() not in {".apk", ".apkm", ".xapk"}:
        import zipfile

        if zipfile.is_zipfile(final_path):
            with zipfile.ZipFile(final_path) as zf:
                names = zf.namelist()
            if "AndroidManifest.xml" in names:
                better = final_path.with_suffix(".apk")
            elif any(n.endswith(".apk") for n in names):
                better = final_path.with_suffix(".apkm")
            else:
                better = None
            if better is not None and not better.exists():
                final_path.rename(better)
                final_path = better

    return final_path


async def _attempt_download(session: str, variant_url: str, app_config: dict, out_dir: Path) -> Path:
    variant_solution = await _fetch(session, variant_url, wait_seconds=1.2, label="variant-page")

    href = parser.download_button_href(variant_solution.html)
    if not href or href.startswith(("javascript:", "#")):
        raise _ChallengePresent(_register_challenge())

    download_page_url = urljoin(variant_solution.url, href)

    try:
        return await _download_file(
            download_page_url, parser.cookie_map(variant_solution.cookies), variant_solution.user_agent, out_dir
        )
    except _DownloadNotAFile:
        log.notice("Direct download did not start, resolving confirm page via FlareSolverr...")

    confirm_solution = await _fetch(
        session,
        download_page_url,
        wait_seconds=1.5,
        label="download-confirm",
        max_timeout_ms=FLARESOLVERR_CONFIRM_TIMEOUT_MS,
        tabs_till_verify=app_config.get("tabs_till_verify"),
    )

    final_href = parser.confirm_link_href(confirm_solution.html)
    if not final_href:
        raise _ChallengePresent(_register_challenge())

    file_url = urljoin(confirm_solution.url, final_href)
    try:
        return await _download_file(
            file_url, parser.cookie_map(confirm_solution.cookies), confirm_solution.user_agent, out_dir
        )
    except _DownloadNotAFile as e:
        raise _ChallengePresent(_register_challenge()) from e


async def download_apk(version: str, app_name: str = "youtube", force_build: str | None = None) -> str:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    out_dir = Path(__file__).resolve().parent.parent.parent / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = await _get_session()
    variant_url = None

    try:
        list_url, is_final = await _resolve_list_url(session, app_config, version)
        log.info(f"LIST: {list_url}")

        if is_final:
            variant_url = list_url
            log.info(f"VARIANT: {variant_url} (single-variant release)")
        else:
            variant_url = None
            solution = None
            for attempt in range(4):
                solution = await _fetch(session, list_url, wait_seconds=1.5 + attempt * 1.0, label="list-page")
                variant_url = parser.extract_variant_url(solution.html, force_build, app_name)
                if variant_url:
                    break
                log.notice(f"No matching row found on page, retrying ({attempt + 1}/4)...")

            if not variant_url:
                if solution is not None:
                    for line in parser.dump_variant_rows(solution.html):
                        log.info(line)
                await _save_diagnostics(session, list_url, f"no-variant-{app_name}")
                raise RuntimeError("No matching variant found on APKMirror")
            if variant_url.startswith("/"):
                variant_url = "https://www.apkmirror.com" + variant_url

            log.info(f"VARIANT: {variant_url}")

        log.browser("Resolving download link via FlareSolverr...")
        download_path = None
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
                    download_path = await _attempt_download(session, variant_url, app_config, out_dir)
        except _ChallengePresent:
            pass

        if download_path is None:
            log.error(f"Download did not start. Last known variant page: {variant_url}")
            await _save_diagnostics(session, variant_url, f"no-download-{app_name}")
            raise RuntimeError("Download did not start / file not detected.")

        size = download_path.stat().st_size
        log.success(f"DONE: {download_path} ({size / 1024 / 1024:.2f} MB)")
        return str(download_path)

    except Exception:
        if variant_url:
            await _save_diagnostics(session, variant_url, f"error-{app_name}")
        raise


async def get_latest_listing(app_name: str) -> dict | None:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    session = await _get_session()
    listing_url = f"https://www.apkmirror.com/apk/{app_config['org']}/{app_config['slug']}/"

    try:
        log.info(f"LISTING: {listing_url}")

        candidates: list[dict] = []
        for attempt in range(4):
            solution = await _fetch(session, listing_url, wait_seconds=2.5 + attempt * 1.2, label="app-listing")
            candidates = parser.listing_candidates(solution.html)
            if candidates:
                break
            log.notice(f"No link found on listing page, retrying ({attempt + 1}/4)...")

        if not candidates:
            await _save_diagnostics(session, listing_url, f"no-listing-{app_name}")
            return None

        for item in candidates:
            href = item.get("href")
            text = item.get("text", "")

            version = parser.version_from_href(href)
            if not version:
                match = re.search(r"\d+(?:\.\d+)+", text)
                version = match.group(0) if match else None

            if version:
                return {"version": version, "href": href}

        await _save_diagnostics(session, listing_url, f"no-version-{app_name}")
        return None

    except Exception:
        await _save_diagnostics(session, listing_url, f"error-listing-{app_name}")
        raise
