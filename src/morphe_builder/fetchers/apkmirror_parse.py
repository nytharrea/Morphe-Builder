"""HTML parsing and variant-row selection for APKMirror pages. No network
calls and no retry/challenge logic live here - this module only turns
already-fetched HTML into the pieces apkmirror.py's orchestration needs
(a variant's download-page URL, whether a page is a 404, a version number
parsed out of a listing link).
"""

import re
from urllib.parse import urljoin

import lxml.html

from .. import log

_ALLOWED_ARCHS = [
    "universal",
    "evrensel",
    "noarch",
    "arm64-v8a",
    "arm64-v8a + armeabi-v7a",
    "arm64-v8a + armeabi",
]


def parse(html: str) -> lxml.html.HtmlElement | None:
    if not html:
        return None
    try:
        return lxml.html.fromstring(html)
    except Exception:
        return None


def abs_url(base: str, href: str | None) -> str | None:
    return urljoin(base, href) if href else None


def page_text(tree: lxml.html.HtmlElement | None, limit: int) -> str:
    if tree is None:
        return ""
    title = tree.findtext(".//title") or ""
    body = tree.find(".//body")
    body_text = " ".join(body.itertext())[:limit] if body is not None else ""
    return f"{title} {body_text}".lower()


def is_404_html(tree: lxml.html.HtmlElement | None) -> bool:
    content = page_text(tree, 300)
    if "404" not in content:
        return False
    return "whoops" in content or "could not be found" in content or "not be found" in content


def classes(el) -> list[str]:
    return list(getattr(el, "classes", None) or [])


def cell_text(cell) -> str:
    return (cell.text_content() or "").strip() if cell is not None else ""


def closest(el, tags: set[str]):
    node = el
    while node is not None:
        if node.tag in tags:
            return node
        node = node.getparent()
    return None


def variant_rows(tree: lxml.html.HtmlElement | None) -> list:
    if tree is None:
        return []
    rows = []
    for el in tree.iter():
        if "table-row" not in classes(el):
            continue
        ancestor = el.getparent()
        while ancestor is not None:
            if "variants-table" in classes(ancestor):
                rows.append(el)
                break
            ancestor = ancestor.getparent()
    return rows


def row_count(tree: lxml.html.HtmlElement | None) -> int:
    return len(variant_rows(tree))


def has_download_button(tree: lxml.html.HtmlElement | None) -> bool:
    if tree is None:
        return False
    return any("downloadButton" in classes(a) for a in tree.iter("a"))


def extract_variant_url(tree: lxml.html.HtmlElement | None, force_build: str | None, app_slug: str) -> str | None:
    candidates: list[str | None] = [None] * 6

    for row in variant_rows(tree):
        cells = [c for c in row.iterchildren() if "table-cell" in classes(c)]
        if len(cells) < 4:
            continue

        link = next((a for a in cells[0].iter("a") if "accent_color" in classes(a)), None)
        if link is None:
            continue

        if force_build and force_build not in cell_text(cells[0]):
            continue

        badge = next((b for b in cells[0].iter() if "apkm-badge" in classes(b)), None)
        badge_text = cell_text(badge).upper()
        is_bundle = "BUNDLE" in badge_text or "PAKET" in badge_text

        if app_slug == "instagram" and not is_bundle:
            continue

        arch_text = cell_text(cells[1]).lower()
        dpi_text = cell_text(cells[3]).lower()

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


def dump_variant_rows_for_debug(tree: lxml.html.HtmlElement | None) -> None:
    all_rows = [el for el in (tree.iter() if tree is not None else []) if "table-row" in classes(el)]
    scoped_rows = variant_rows(tree)

    log.info(
        f"Debug: page has {len(all_rows)} .table-row elements "
        f"({len(scoped_rows)} of them inside the real .variants-table), is404: {is_404_html(tree)}"
    )
    for i, row in enumerate(scoped_rows[:20]):
        cells = [c for c in row.iterchildren() if "table-cell" in classes(c)]
        name = cell_text(cells[0])[:60] if len(cells) > 0 else None
        arch = cell_text(cells[1]) if len(cells) > 1 else None
        dpi = cell_text(cells[3]) if len(cells) > 3 else None
        log.info(f"   [{i}] cells={len(cells)} name={name!r} arch={arch!r} dpi={dpi!r}")


def find_listing_link(tree: lxml.html.HtmlElement, base_url: str, slug_part: str) -> str | None:
    for a in tree.iter("a"):
        href = a.get("href")
        if href and "-release/" in href and slug_part in href and "#" not in href:
            return abs_url(base_url, href)
    return None


def version_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r"-(\d[\d]*(?:-\d+)+)-release", href)
    if not match:
        return None
    return match.group(1).replace("-", ".")


def listing_candidates(tree: lxml.html.HtmlElement, base_url: str) -> list[tuple[str, str]]:
    results = []
    for a in tree.iter("a"):
        href = a.get("href")
        if not href or "-release/" not in href:
            continue
        row = closest(a, {"div", "li", "tr"})
        if row is None:
            row = a.getparent() if a.getparent() is not None else a
        abs_href = abs_url(base_url, href)
        if abs_href:
            results.append((abs_href, row.text_content() or ""))
        if len(results) >= 15:
            break
    return results
