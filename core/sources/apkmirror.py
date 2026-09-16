from __future__ import annotations

import asyncio
import random
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from curl_cffi.requests import AsyncSession
from lxml import html as lxml_html
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.stop import stop_base

from .. import flaresolverr, log
from .. import retry as retry_conf
from ..apk.versions import to_apkmirror_version
from ..settings import settings

APP_SITES = {
    "youtube": {"org": "google-inc", "slug": "youtube"},
    "youtube-music": {"org": "google-inc", "slug": "youtube-music"},
    "reddit": {"org": "reddit-inc", "slug": "reddit"},
    "twitter": {"org": "x-corp", "slug": "twitter", "release_slug": "x"},
    "instagram": {"org": "instagram", "slug": "instagram-instagram", "release_slug": "instagram"},
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
}

DIAGNOSTICS_DIR = Path(__file__).resolve().parent.parent.parent / "diagnostics"
RESOLVE_BUDGET_SECONDS = 300.0
BASE_URL = "https://www.apkmirror.com"

_challenge_hits = 0
_cooldown_until = 0.0


async def _jitter_sleep(base: float, spread: float = 0.6) -> None:
    await asyncio.sleep(base + random.uniform(0, spread))


async def _apply_global_cooldown() -> None:
    remaining = _cooldown_until - time.monotonic()
    if remaining > 0:
        log.wait(f"Global APKMirror cooldown active, sleeping {remaining:.0f}s...")
        await asyncio.sleep(remaining)


def _register_challenge() -> float:
    global _challenge_hits, _cooldown_until
    _challenge_hits += 1
    cooldown = min(60.0, 10.0 * _challenge_hits)
    _cooldown_until = time.monotonic() + cooldown
    return cooldown


class _BudgetExceeded(stop_base):
    def __init__(self, deadline: float | None):
        self.deadline = deadline

    def __call__(self, retry_state) -> bool:
        if self.deadline is None or retry_state.outcome is None:
            return False
        sleep = retry_state.next_action.sleep if retry_state.next_action else 0.0
        return time.monotonic() + sleep >= self.deadline


async def _get(url: str, *, label: str, deadline: float | None = None) -> dict[str, Any]:
    await _apply_global_cooldown()
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.flaresolverr_retries) | _BudgetExceeded(deadline),
            wait=retry_conf.incrementing(start=2.0, increment=2.0, max=15.0),
            retry=retry_if_exception_type(flaresolverr.FlareSolverrChallengeError),
            before_sleep=lambda rs: log.notice(
                f"FlareSolverr challenge on {label}, cooling down "
                f"{(rs.next_action.sleep if rs.next_action else 0):.0f}s before retry..."
            ),
            reraise=True,
        ):
            with attempt:
                return await flaresolverr.request_get(url)
    except flaresolverr.FlareSolverrChallengeError:
        cooldown = _register_challenge()
        log.warn(
            f"Challenge still present ({label}); backing off {cooldown:.0f}s and continuing with returned HTML."
        )
        return await flaresolverr.request_get(url)
    raise flaresolverr.FlareSolverrError(f"Could not fetch {label} from APKMirror")


def _html_document(solution: dict[str, Any]):
    response = solution.get("response") or ""
    return lxml_html.fromstring(response)


def _absolute(href: str | None, base: str) -> str | None:
    if not href:
        return None
    return urljoin(base, href)


def _is_404(solution: dict[str, Any], doc) -> bool:
    if solution.get("status") == 404:
        return True
    title = " ".join(doc.xpath("//title//text()"))
    body = " ".join(doc.xpath("//body//text()"))[:1000]
    lowered = f"{title} {body}".lower()
    return "404" in lowered and (
        "whoops" in lowered or "not be found" in lowered or "could not be found" in lowered
    )


def _row_count(doc) -> int:
    return len(doc.xpath("//*[contains(@class,'variants-table')]//*[contains(@class,'table-row')]"))


def _has_download_button(doc) -> bool:
    return bool(doc.xpath("//a[contains(@class,'downloadButton')]"))


async def _page_exists(url: str, deadline: float | None = None) -> bool:
    try:
        solution = await _get(url, label="direct-try", deadline=deadline)
        doc = _html_document(solution)
        if _is_404(solution, doc):
            return False
        return _row_count(doc) > 0
    except Exception:
        return False


async def _resolve_list_url(app_config: dict, version: str) -> tuple[str, bool]:
    version_slug = to_apkmirror_version(version)
    name_part = app_config.get("release_slug") or app_config["slug"]
    folder_url = f"{BASE_URL}/apk/{app_config['org']}/{app_config['slug']}"
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
            solution = await _get(direct_variant, label="direct-variant-try", deadline=deadline)
            doc = _html_document(solution)
            if not _is_404(solution, doc) and _has_download_button(doc):
                return direct_variant, True
        except Exception:
            pass

    if time.monotonic() > deadline:
        raise RuntimeError(
            f"Giving up on {app_config['slug']} v{version}: APKMirror/FlareSolverr resolve budget "
            f"{RESOLVE_BUDGET_SECONDS:.0f}s exceeded."
        )

    log.search("No direct match, scanning app listing page...")
    listing_url = f"{folder_url}/"
    slug_part = f"-{version_slug}-"
    for _attempt in range(2):
        if time.monotonic() > deadline:
            break
        solution = await _get(listing_url, label="listing-scan", deadline=deadline)
        doc = _html_document(solution)
        for href in doc.xpath("//a[contains(@href,'-release/')]/@href"):
            href = _absolute(href, listing_url)
            if href and slug_part in href and "#" not in href:
                return href, False
    raise RuntimeError(f"No APKMirror release page found for version {version}")


def _text(node) -> str:
    return " ".join(t.strip() for t in node.xpath(".//text()") if t.strip())


def _extract_variant_url(
    solution: dict[str, Any], force_build: str | None, app_name: str, base_url: str
) -> str | None:
    doc = _html_document(solution)
    rows = doc.xpath("//*[contains(@class,'variants-table')]//*[contains(@class,'table-row')]")
    allowed_archs = (
        "universal",
        "evrensel",
        "noarch",
        "arm64-v8a",
        "arm64-v8a + armeabi-v7a",
        "arm64-v8a + armeabi",
    )

    def collect(use_force_build: bool) -> str | None:
        candidates: list[str | None] = [None] * 8
        for row in rows:
            cells = row.xpath(".//*[contains(@class,'table-cell')]")
            if len(cells) < 4:
                continue
            hrefs = cells[0].xpath(".//a[contains(@class,'accent_color')]/@href")
            if not hrefs:
                continue
            name_text = _text(cells[0])
            if use_force_build and force_build and force_build not in name_text:
                continue
            badge_text = " ".join(cells[0].xpath(".//*[contains(@class,'apkm-badge')]//text()")).upper()
            is_bundle = "BUNDLE" in badge_text or "PAKET" in badge_text
            if app_name == "instagram" and not is_bundle:
                continue
            arch_text = _text(cells[1]).lower()
            dpi_text = _text(cells[3]).lower()
            is_target_arch = not arch_text or any(a in arch_text for a in allowed_archs)
            if not is_target_arch:
                continue
            is_universal = "universal" in arch_text or "evrensel" in arch_text
            is_nodpi = not dpi_text or "nodpi" in dpi_text
            is_anydpi = "anydpi" in dpi_text
            if is_universal:
                slot = 0 if is_bundle else 1
            elif is_nodpi:
                slot = 2 if is_bundle else 3
            elif is_anydpi:
                slot = 4 if is_bundle else 5
            else:
                slot = 6 if is_bundle else 7
            if candidates[slot] is None:
                candidates[slot] = _absolute(hrefs[0], base_url)
        return next((c for c in candidates if c), None)

    result = collect(True)
    if not result and force_build:
        result = collect(False)
    return result


def _dump_variant_rows_for_debug(solution: dict[str, Any]) -> None:
    try:
        doc = _html_document(solution)
        rows = doc.xpath("//*[contains(@class,'table-row')]")[:20]
        log.info(f"Debug: page has {len(doc.xpath("//*[contains(@class, 'table-row')]"))} .table-row elements")
        for i, row in enumerate(rows):
            cells = row.xpath(".//*[contains(@class,'table-cell')]")
            log.info(
                f"   [{i}] cells={len(cells)} name={_text(cells[0])[:60]!r} "
                f"arch={_text(cells[1]) if len(cells) > 1 else ''!r} "
                f"dpi={_text(cells[3]) if len(cells) > 3 else ''!r}"
            )
    except Exception as e:
        log.warn(f"Could not produce debug dump: {e}")


def _save_diagnostic_html(solution: dict[str, Any], name: str) -> None:
    try:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        path = DIAGNOSTICS_DIR / f"{int(time.time())}-{name}.html"
        path.write_text(solution.get("response") or "", encoding="utf-8", errors="replace")
        log.warn(f"Diagnostic HTML saved: {path}")
    except Exception as e:
        log.warn(f"Could not save diagnostic HTML: {e}")


def _filename_from_content_disposition(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', value, flags=re.IGNORECASE)
    if not match:
        return fallback
    name = match.group(1).strip().strip('"')
    return name or fallback


def _looks_like_apk_container(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"PK"
    except OSError:
        return False


def _extract_base_apk_if_needed(path: Path) -> Path:
    """APKMirror sometimes serves an .apkm/.xapk bundle from download.php.

    Morphe Desktop expects a real APK with AndroidManifest.xml at the ZIP root.
    If the downloaded container is a bundle, extract base.apk and return that.
    """
    if not zipfile.is_zipfile(path):
        if _looks_like_apk_container(path):
            return path
        raise RuntimeError(f"Downloaded file is not an APK/ZIP container: {path}")

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "AndroidManifest.xml" in names:
            return path
        candidates = [n for n in names if n.split("/")[-1] == "base.apk"]
        if not candidates:
            candidates = [n for n in names if n.endswith(".apk")]
        if not candidates:
            raise RuntimeError(f"Downloaded bundle has no base.apk/AndroidManifest.xml: {path.name}")
        extracted = zf.read(candidates[0])

    base_path = path.with_name(f"{path.stem}-base.apk")
    base_path.write_bytes(extracted)
    log.info(f"Extracted base APK from bundle: {base_path.name}")
    return base_path


def _download_headers(solution: dict[str, Any]) -> dict[str, str]:
    headers = {"User-Agent": solution.get("userAgent") or "Mozilla/5.0"}
    cookies = solution.get("cookies") or []
    if cookies:
        headers["Cookie"] = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name"))
    return headers


async def _download_file(url: str, out_path: Path, solution: dict[str, Any]) -> Path:
    temp_path = out_path.with_name(out_path.name + ".part")
    headers = _download_headers(solution)
    file_name = out_path.name
    async with (
        AsyncSession(timeout=None, allow_redirects=True, impersonate="firefox", headers=headers) as client,  # type: ignore[arg-type]
        client.stream("GET", url) as res,
    ):
        if res.status_code >= 400:
            raise RuntimeError(f"APKMirror download HTTP {res.status_code}")
        file_name = _filename_from_content_disposition(res.headers.get("content-disposition"), file_name)
        final_path = out_path.with_name(file_name)
        with open(temp_path, "wb") as f:
            async for chunk in res.aiter_content():
                f.write(chunk)

    size = temp_path.stat().st_size
    if size < 1024:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file too small ({size} bytes)")

    final_path = out_path.with_name(file_name)
    temp_path.replace(final_path)
    patched_input = _extract_base_apk_if_needed(final_path)
    if patched_input != final_path:
        log.info(f"Downloaded bundle container kept at {final_path.name}; using extracted APK for patching")
    return patched_input


async def _resolve_file_url(variant_url: str, solution: dict[str, Any]) -> str:
    doc = _html_document(solution)
    hrefs = doc.xpath("//a[contains(@class,'downloadButton')]/@href")
    if not hrefs:
        raise RuntimeError(f"No a.downloadButton found on variant page: {variant_url}")
    confirm_url = _absolute(hrefs[0], variant_url)
    if not confirm_url:
        raise RuntimeError("Could not resolve download button URL")

    if confirm_url.lower().endswith(".apk") or "download.php" in confirm_url:
        return confirm_url

    confirm_solution = await _get(confirm_url, label="download-confirm")
    confirm_doc = _html_document(confirm_solution)
    final_hrefs = confirm_doc.xpath("//a[@id='download-link']/@href")
    if not final_hrefs:
        final_hrefs = [
            h for h in confirm_doc.xpath("//a/@href") if h.lower().endswith(".apk") or "download.php" in h
        ]
    if not final_hrefs:
        raise RuntimeError(f"No #download-link found on confirm page: {confirm_url}")
    file_url = _absolute(final_hrefs[0], confirm_url)
    if not file_url:
        raise RuntimeError("Could not resolve final download URL")
    return file_url


async def download_apk(version: str, app_name: str = "youtube", force_build: str | None = None) -> str:
    app_config = APP_SITES.get(app_name)
    if not app_config:
        raise RuntimeError(f'Unknown appName "{app_name}" - not found in APP_SITES')

    out_dir = Path(__file__).resolve().parent.parent.parent / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)

    list_url, is_final = await _resolve_list_url(app_config, version)
    log.info(f"LIST: {list_url}")

    variant_url: str | None
    if is_final:
        variant_solution = await _get(list_url, label="variant-page")
        variant_url = list_url
        log.info(f"VARIANT: {variant_url} (single-variant release)")
    else:
        variant_solution = None
        variant_url = None
        for attempt in range(4):
            solution = await _get(list_url, label="list-page")
            variant_url = _extract_variant_url(solution, force_build, app_name, list_url)
            if variant_url:
                variant_solution = solution
                break
            log.notice(f"No matching row found on page, retrying ({attempt + 1}/4)...")
        if not variant_url or variant_solution is None:
            last_solution = await _get(list_url, label="list-page-debug")
            _dump_variant_rows_for_debug(last_solution)
            _save_diagnostic_html(last_solution, f"no-variant-{app_name}")
            raise RuntimeError("No matching variant found on APKMirror")
        log.info(f"VARIANT: {variant_url}")
        variant_solution = await _get(variant_url, label="variant-page")

    assert variant_solution is not None
    resolved_variant_url: str = variant_url
    file_url = await _resolve_file_url(resolved_variant_url, variant_solution)
    log.download(f"Downloading: {file_url}")

    file_name = Path(file_url.split("?")[0]).name or f"{app_name}-{version}.apk"
    if not file_name.lower().endswith((".apk", ".apkm", ".xapk")):
        file_name = f"{app_name}-{version}.apk"
    final_path = out_dir / file_name

    downloaded_path = await _download_file(file_url, final_path, variant_solution)
    size = downloaded_path.stat().st_size
    log.success(f"DONE: {downloaded_path} ({size / 1024 / 1024:.2f} MB)")
    return str(downloaded_path)


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

    listing_url = f"{BASE_URL}/apk/{app_config['org']}/{app_config['slug']}/"
    log.info(f"LISTING: {listing_url}")

    candidates: list[dict[str, Any]] = []
    for attempt in range(4):
        solution = await _get(listing_url, label="app-listing")
        doc = _html_document(solution)
        for link in doc.xpath("//a[contains(@href,'-release/')]")[:15]:
            href = _absolute(link.get("href"), listing_url)
            row = link.xpath("ancestor::div[1] | ancestor::li[1] | ancestor::tr[1]")
            text = _text(row[0]) if row else _text(link)
            candidates.append({"href": href, "text": text})
        if candidates:
            break
        log.notice(f"No link found on listing page, retrying ({attempt + 1}/4)...")

    if not candidates:
        return None

    for item in candidates:
        href = item.get("href")
        text = item.get("text", "")
        version = _version_from_href(href)
        if not version:
            match = re.search(r"\d+(?:\.\d+)+", text)
            version = match.group(0) if match else None
        if version:
            return {"version": version, "href": href}
    return None


async def close_browser() -> None:
    """Backward-compatible no-op kept for main.py.

    FlareSolverr sessions are server-side; there is no local browser to close.
    """
    return None
