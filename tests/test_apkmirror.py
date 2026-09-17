"""APKMirror ayrıştırıcı birim testleri."""

from unittest.mock import MagicMock

from core.sources.apkmirror import APKMirrorSourceProvider


def test_extract_version_page_url():
    sample_html = """
    <div class="appRow">
        <a class="fontBlack" href="/apk/instagram/instagram-android/instagram-android-300-release/">Instagram 300.0.0.0</a>
    </div>
    """
    provider = APKMirrorSourceProvider(MagicMock())
    url = provider._extract_version_page_url(sample_html, "instagram-android", None)
    assert "instagram-android-300-release" in url


def test_skip_beta_version():
    sample_html = """
    <div class="appRow">
        <a class="fontBlack" href="/apk/instagram/instagram-android/instagram-beta/">Instagram 301.0.0.0 Beta</a>
    </div>
    <div class="appRow">
        <a class="fontBlack" href="/apk/instagram/instagram-android/instagram-stable/">Instagram 300.0.0.0</a>
    </div>
    """
    provider = APKMirrorSourceProvider(MagicMock())
    url = provider._extract_version_page_url(sample_html, "instagram-android", None)
    assert "instagram-stable" in url
