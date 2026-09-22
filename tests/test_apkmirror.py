from morphe_builder.fetchers.apkmirror import (
    _closest,
    _extract_variant_url,
    _is_404_html,
    _listing_candidates,
    _looks_like_challenge,
    _parse,
    _variant_rows,
    _version_from_href,
)


def _row(name, arch, dpi, href, badge=None):
    badge_html = f'<span class="apkm-badge">{badge}</span>' if badge else ""
    return f"""
    <div class="table-row">
      <div class="table-cell">{badge_html}<a class="accent_color" href="{href}">{name}</a></div>
      <div class="table-cell">{arch}</div>
      <div class="table-cell">type</div>
      <div class="table-cell">{dpi}</div>
    </div>
    """


def test_extract_variant_url_prefers_non_bundle_nodpi():
    html = f"""<html><body><div class="variants-table">
        {_row("Foo 1.0 arm64-v8a", "arm64-v8a", "nodpi", "/normal-nodpi")}
        {_row("Foo 1.0 x86", "x86", "nodpi", "/wrong-arch")}
        {_row("Foo 1.0 Bundle", "arm64-v8a + armeabi-v7a", "nodpi", "/bundle-nodpi", badge="APK Bundle")}
    </div></body></html>"""
    assert _extract_variant_url(_parse(html), None, "youtube") == "/normal-nodpi"


def test_extract_variant_url_falls_back_to_anydpi_then_specific():
    html = f"""<html><body><div class="variants-table">
        {_row("Foo 1.0", "universal", "160-640dpi", "/specific-dpi")}
        {_row("Foo 1.0", "universal", "anydpi", "/anydpi")}
    </div></body></html>"""
    assert _extract_variant_url(_parse(html), None, "youtube") == "/anydpi"


def test_extract_variant_url_instagram_skips_non_bundle_rows():
    html = f"""<html><body><div class="variants-table">
        {_row("Instagram 1.0 arm64-v8a", "arm64-v8a", "nodpi", "/should-be-skipped")}
        {_row("Instagram 1.0 Bundle", "arm64-v8a + armeabi-v7a", "nodpi", "/insta-bundle", badge="APK Bundle")}
    </div></body></html>"""
    assert _extract_variant_url(_parse(html), None, "instagram") == "/insta-bundle"


def test_extract_variant_url_instagram_with_no_bundle_row_returns_none():
    html = f"""<html><body><div class="variants-table">
        {_row("Instagram 1.0 arm64-v8a", "arm64-v8a", "nodpi", "/no-bundle-here")}
    </div></body></html>"""
    assert _extract_variant_url(_parse(html), None, "instagram") is None


def test_extract_variant_url_respects_force_build():
    html = f"""<html><body><div class="variants-table">
        {_row("Foo build1234 arm64-v8a", "arm64-v8a", "nodpi", "/build1234")}
        {_row("Foo build5678 arm64-v8a", "arm64-v8a", "nodpi", "/build5678")}
    </div></body></html>"""
    assert _extract_variant_url(_parse(html), "build5678", "youtube") == "/build5678"


def test_variant_rows_ignores_rows_outside_variants_table():
    html = """<html><body>
        <div class="table-row">not scoped, should be ignored</div>
        <div class="variants-table"><div class="table-row">scoped</div></div>
    </body></html>"""
    rows = _variant_rows(_parse(html))
    assert len(rows) == 1
    assert rows[0].text_content().strip() == "scoped"


def test_is_404_html_detects_apkmirror_not_found_page():
    html = "<html><head><title>404 - Whoops! That page can&#8217;t be found.</title></head><body></body></html>"
    assert _is_404_html(_parse(html)) is True


def test_is_404_html_false_for_normal_page():
    html = "<html><head><title>YouTube 19.35.36</title></head><body>Download options</body></html>"
    assert _is_404_html(_parse(html)) is False


def test_looks_like_challenge_detects_cloudflare_interstitial():
    html = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
    assert _looks_like_challenge(html) is True


def test_looks_like_challenge_false_for_normal_page():
    assert _looks_like_challenge("<html><body>Normal content</body></html>") is False


def test_version_from_href_extracts_dotted_version():
    href = "/apk/google-inc/youtube/youtube-19-35-36-release/"
    assert _version_from_href(href) == "19.35.36"


def test_version_from_href_returns_none_without_match():
    assert _version_from_href("/apk/google-inc/youtube/") is None


def test_listing_candidates_resolves_relative_hrefs():
    html = """<html><body>
        <div><a href="/apk/google-inc/youtube/youtube-19-35-36-release/">YouTube 19.35.36</a></div>
    </body></html>"""
    candidates = _listing_candidates(_parse(html), "https://www.apkmirror.com/apk/google-inc/youtube/")
    assert candidates == [
        ("https://www.apkmirror.com/apk/google-inc/youtube/youtube-19-35-36-release/", "YouTube 19.35.36")
    ]


def test_closest_walks_up_to_matching_ancestor():
    html = '<html><body><tr><td><a href="#">link</a></td></tr></body></html>'
    a = _parse(html).find(".//a")
    assert _closest(a, {"div", "li", "tr"}).tag == "tr"


def test_closest_returns_none_when_no_ancestor_matches():
    html = '<html><body><span><a href="#">link</a></span></body></html>'
    a = _parse(html).find(".//a")
    assert _closest(a, {"div", "li", "tr"}) is None
