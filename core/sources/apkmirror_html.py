import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import lxml.html

ALLOWED_ARCHES = [
    "universal",
    "evrensel",
    "noarch",
    "arm64-v8a",
    "arm64-v8a + armeabi-v7a",
    "arm64-v8a + armeabi",
]

CHALLENGE_MARKERS = [
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

_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)


def parse(html: str):
    if not html:
        return None
    try:
        return lxml.html.fromstring(html)
    except Exception:
        return None


def by_class(el, cls: str) -> list:
    return el.xpath(f".//*[contains(concat(' ', normalize-space(@class), ' '), ' {cls} ')]")


def cell_text(cell) -> str:
    return (cell.text_content() or "").strip()


def _title_and_body(tree, body_chars: int) -> str:
    title = tree.findtext(".//title") or ""
    body = tree.find(".//body")
    body_text = body.text_content()[:body_chars] if body is not None else ""
    return f"{title} {body_text}".lower()


def is_challenge_page(html: str) -> bool:
    tree = parse(html)
    if tree is None:
        return False
    content = _title_and_body(tree, 500)
    return any(marker in content for marker in CHALLENGE_MARKERS)


def is_404_page(html: str) -> bool:
    tree = parse(html)
    if tree is None:
        return False
    lowered = _title_and_body(tree, 300)
    return "404" in lowered and (
        "whoops" in lowered or "could not be found" in lowered or "not be found" in lowered
    )


def variant_rows(tree) -> list:
    rows = []
    for table in by_class(tree, "variants-table"):
        rows.extend(by_class(table, "table-row"))
    return rows


def row_count(html: str) -> int:
    tree = parse(html)
    if tree is None:
        return 0
    return len(variant_rows(tree))


def has_download_button(html: str) -> bool:
    tree = parse(html)
    if tree is None:
        return False
    return bool(tree.xpath("//a[contains(concat(' ', normalize-space(@class), ' '), ' downloadButton ')]"))


def download_button_href(html: str) -> str | None:
    tree = parse(html)
    if tree is None:
        return None
    hrefs = tree.xpath("//a[contains(concat(' ', normalize-space(@class), ' '), ' downloadButton ')]/@href")
    return str(hrefs[0]) if hrefs else None


def confirm_link_href(html: str) -> str | None:
    tree = parse(html)
    if tree is None:
        return None
    hrefs = tree.xpath("//*[@id='download-link']/@href")
    return str(hrefs[0]) if hrefs else None


def find_release_link(html: str, version_slug: str) -> str | None:
    tree = parse(html)
    if tree is None:
        return None
    slug_part = f"-{version_slug}-"
    for a in tree.xpath("//a[@href]"):
        href = a.get("href") or ""
        if "-release/" in href and slug_part in href and "#" not in href:
            return str(href)
    return None


def listing_candidates(html: str) -> list[dict]:
    tree = parse(html)
    if tree is None:
        return []
    links = [a for a in tree.xpath("//a[@href]") if "-release/" in (a.get("href") or "")][:15]
    candidates = []
    for link in links:
        row = None
        for ancestor in link.iterancestors():
            if ancestor.tag in ("div", "li", "tr"):
                row = ancestor
                break
        if row is None:
            row = link.getparent()
        text = row.text_content() if row is not None else (link.text_content() or "")
        candidates.append({"href": link.get("href"), "text": text or ""})
    return candidates


def extract_variant_url(html: str, force_build: str | None, app_name: str) -> str | None:
    tree = parse(html)
    if tree is None:
        return None

    candidates: list[str | None] = [None] * 6

    for row in variant_rows(tree):
        cells = by_class(row, "table-cell")
        if len(cells) < 4:
            continue

        links = [el for el in by_class(cells[0], "accent_color") if el.tag == "a"]
        if not links or not links[0].get("href"):
            continue
        link = links[0]

        if force_build and force_build not in cell_text(cells[0]):
            continue

        badges = by_class(cells[0], "apkm-badge")
        badge_text = cell_text(badges[0]).upper() if badges else ""
        is_bundle = "BUNDLE" in badge_text or "PAKET" in badge_text

        if app_name == "instagram" and not is_bundle:
            continue

        arch_text = cell_text(cells[1]).lower()
        dpi_text = cell_text(cells[3]).lower()

        is_target_arch = arch_text == "" or any(a in arch_text for a in ALLOWED_ARCHES)
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


def dump_variant_rows(html: str) -> list[str]:
    tree = parse(html)
    if tree is None:
        return ["Could not produce debug dump: page HTML was empty/unparseable"]

    all_rows = by_class(tree, "table-row")
    scoped_rows = variant_rows(tree)
    lowered = _title_and_body(tree, 300)
    is_404 = "404" in lowered or "could not be found" in lowered

    lines = [
        f"Debug: page has {len(all_rows)} .table-row elements "
        f"({len(scoped_rows)} of them inside the real .variants-table), "
        f"is404: {is_404}"
    ]
    for i, row in enumerate(all_rows[:20]):
        cells = by_class(row, "table-cell")
        name = cell_text(cells[0])[:60] if len(cells) > 0 else None
        arch = cell_text(cells[1]) if len(cells) > 1 else None
        dpi = cell_text(cells[3]) if len(cells) > 3 else None
        lines.append(f"   [{i}] cells={len(cells)} name={name!r} arch={arch!r} dpi={dpi!r}")
    return lines


def cookie_map(cookies: list[dict]) -> dict[str, str]:
    return {c["name"]: c["value"] for c in cookies if "name" in c and "value" in c}


def filename_from_response(url: str, headers) -> str:
    disposition = headers.get("content-disposition") or headers.get("Content-Disposition")
    if disposition:
        match = _FILENAME_RE.search(disposition)
        if match:
            return unquote(match.group(1))
    name = Path(urlparse(url).path).name
    # APKMirror CDN URLs are often .../download.php?id=... — keep a usable name.
    if not name or name.lower() in {"download.php", "download", "index.php"}:
        return "download.apk"
    return name


def version_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r"-(\d[\d]*(?:-\d+)+)-release", href)
    if not match:
        return None
    return match.group(1).replace("-", ".")
