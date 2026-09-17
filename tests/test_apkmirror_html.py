from core.sources import apkmirror_html as p

VARIANT_PAGE = """
<html><head><title>YouTube 19.30.30 APK Download - APKMirror</title></head>
<body>
<div class="variants-table">
  <div class="table-row headerFont">
    <div class="table-cell">Variant</div>
    <div class="table-cell">Arch</div>
    <div class="table-cell">Version</div>
    <div class="table-cell">DPI</div>
  </div>
  <div class="table-row">
    <div class="table-cell"><a class="accent_color" href="/apk/google-inc/youtube/youtube-19-30-30-release/youtube-19-30-30-2-android-apk-download/">YouTube 19.30.30 (arm64-v8a) (nodpi)</a></div>
    <div class="table-cell">arm64-v8a</div>
    <div class="table-cell">Android 5.0+</div>
    <div class="table-cell">nodpi</div>
  </div>
  <div class="table-row">
    <div class="table-cell"><a class="accent_color" href="/apk/google-inc/youtube/youtube-19-30-30-release/youtube-19-30-30-3-android-apk-download/">YouTube 19.30.30 (universal) (nodpi)</a><span class="apkm-badge">APK</span></div>
    <div class="table-cell">universal</div>
    <div class="table-cell">Android 5.0+</div>
    <div class="table-cell">nodpi</div>
  </div>
  <div class="table-row">
    <div class="table-cell"><a class="accent_color" href="/apk/google-inc/youtube/youtube-19-30-30-release/youtube-19-30-30-4-android-apk-download/">YouTube 19.30.30 (bundle) (nodpi)</a><span class="apkm-badge">BUNDLE</span></div>
    <div class="table-cell">universal</div>
    <div class="table-cell">Android 5.0+</div>
    <div class="table-cell">nodpi</div>
  </div>
</div>
</body></html>
"""

DOWNLOAD_CONFIRM_PAGE = """
<html><head><title>YouTube 19.30.30 APK Download</title></head>
<body>
<a id="download-link" rel="nofollow" href="https://download.apkmirror.com/afh/1234/YouTube_19.30.30.apk?key=abcd">click here</a>
</body></html>
"""

VARIANT_PAGE_WITH_BUTTON = """
<html><head><title>YouTube 19.30.30 android-apk-download</title></head>
<body>
<a class="downloadButton" href="/apk/google-inc/youtube/youtube-19-30-30-release/download/?key=xyz">Download APK</a>
</body></html>
"""

CHALLENGE_PAGE = """
<html><head><title>Just a moment...</title></head>
<body>Checking your browser before accessing www.apkmirror.com. This process is automatic.</body></html>
"""

NOT_FOUND_PAGE = """
<html><head><title>404 - Whoops, that page is gone.</title></head>
<body>Whoops! We could not find that page.</body></html>
"""

LISTING_PAGE = """
<html><head><title>YouTube APK - APKMirror</title></head>
<body>
<div class="listWidget">
  <div class="appRow">
    <a href="/apk/google-inc/youtube/youtube-19-30-30-release/">YouTube 19.30.30</a>
  </div>
  <div class="appRow">
    <a href="/apk/google-inc/youtube/youtube-19-29-34-release/">YouTube 19.29.34</a>
  </div>
</div>
</body></html>
"""


def test_extract_variant_url_prefers_non_bundle_nodpi():
    url = p.extract_variant_url(VARIANT_PAGE, None, "youtube")
    assert url is not None
    assert "youtube-19-30-30-2-" in url


def test_extract_variant_url_forces_bundle_for_instagram():
    url = p.extract_variant_url(VARIANT_PAGE, None, "instagram")
    assert url is not None
    assert "youtube-19-30-30-4-" in url


def test_extract_variant_url_respects_force_build_filter():
    url = p.extract_variant_url(VARIANT_PAGE, "universal", "youtube")
    assert url is not None
    assert "youtube-19-30-30-3-" in url


def test_extract_variant_url_none_when_no_row_matches():
    assert p.extract_variant_url(NOT_FOUND_PAGE, None, "youtube") is None


def test_row_count_counts_rows_inside_variants_table():
    assert p.row_count(VARIANT_PAGE) == 4


def test_has_download_button():
    assert p.has_download_button(VARIANT_PAGE_WITH_BUTTON) is True
    assert p.has_download_button(VARIANT_PAGE) is False


def test_download_button_href_extracts_href():
    href = p.download_button_href(VARIANT_PAGE_WITH_BUTTON)
    assert href == "/apk/google-inc/youtube/youtube-19-30-30-release/download/?key=xyz"


def test_confirm_link_href_extracts_download_link():
    href = p.confirm_link_href(DOWNLOAD_CONFIRM_PAGE)
    assert href == "https://download.apkmirror.com/afh/1234/YouTube_19.30.30.apk?key=abcd"


def test_confirm_link_href_none_when_absent():
    assert p.confirm_link_href(VARIANT_PAGE) is None


def test_is_challenge_page():
    assert p.is_challenge_page(CHALLENGE_PAGE) is True
    assert p.is_challenge_page(VARIANT_PAGE) is False


def test_is_404_page():
    assert p.is_404_page(NOT_FOUND_PAGE) is True
    assert p.is_404_page(VARIANT_PAGE) is False


def test_find_release_link_matches_version_slug():
    found = p.find_release_link(LISTING_PAGE, "19-30-30")
    assert found == "/apk/google-inc/youtube/youtube-19-30-30-release/"


def test_find_release_link_none_when_no_match():
    assert p.find_release_link(LISTING_PAGE, "99-99-99") is None


def test_cookie_map_builds_dict_and_skips_malformed_entries():
    cookies = p.cookie_map([{"name": "cf_clearance", "value": "abc123"}, {"bad": "entry"}])
    assert cookies == {"cf_clearance": "abc123"}


def test_filename_from_response_reads_content_disposition():
    headers = {"content-disposition": 'attachment; filename="YouTube_19.30.30.apk"'}
    fname = p.filename_from_response("https://download.apkmirror.com/afh/1234/x.apk?key=abcd", headers)
    assert fname == "YouTube_19.30.30.apk"


def test_filename_from_response_falls_back_to_url_path():
    fname = p.filename_from_response("https://download.apkmirror.com/afh/1234/Fallback.apk", {})
    assert fname == "Fallback.apk"


def test_dump_variant_rows_returns_readable_lines():
    lines = p.dump_variant_rows(VARIANT_PAGE)
    assert len(lines) > 1
    assert "table-row" in lines[0]


def test_parse_returns_none_on_empty_html():
    assert p.parse("") is None
    assert p.parse(None) is None
