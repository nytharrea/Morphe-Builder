import io
import zipfile

from morphe_builder.fetchers import flaresolverr

DOWNLOAD_URL = "https://www.apkmirror.com/wp-content/themes/APKMirror/download.php?id=1&key=2"


def _zip_bytes(*names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return buffer.getvalue()


def _zip_bytes_realistic(*names):
    """Like _zip_bytes, but padded well past download_file()'s minimum-size
    floor (1024 bytes - the same threshold apkmirror.py itself already
    applies to a finished download) so the download_file() tests below
    exercise a payload that actually looks like a real download instead of
    tripping the new too-small check on a stand-in that always was tiny."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "x" * 2000)
    return buffer.getvalue()


def _write_zip(path, *names):
    path.write_bytes(_zip_bytes(*names))
    return path


def test_plain_apk_is_named_after_the_app(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "AndroidManifest.xml", "classes.dex")
    result = flaresolverr._name_by_content(saved, "youtube.apk")
    assert result == tmp_path / "youtube.apk"
    assert result.exists()
    assert not saved.exists()


def test_apkm_bundle_gets_apkm_extension(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "info.json", "base.apk", "split_config.arm64_v8a.apk")
    assert flaresolverr._name_by_content(saved, "reddit.apk") == tmp_path / "reddit.apkm"


def test_xapk_bundle_gets_xapk_extension(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "manifest.json", "com.example.apk")
    assert flaresolverr._name_by_content(saved, "example.apk") == tmp_path / "example.xapk"


def test_apks_bundle_gets_apks_extension(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "toc.pb", "splits/base-master.apk")
    assert flaresolverr._name_by_content(saved, "example.apk") == tmp_path / "example.apks"


def test_zip_with_only_apk_entries_is_treated_as_apkm(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "base.apk", "split_config.en.apk")
    assert flaresolverr._name_by_content(saved, "example.apk") == tmp_path / "example.apkm"


def test_manifest_wins_over_bundle_markers(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "AndroidManifest.xml", "info.json", "manifest.json")
    assert flaresolverr._name_by_content(saved, "example.apk") == tmp_path / "example.apk"


def test_unknown_zip_keeps_its_name(tmp_path):
    saved = _write_zip(tmp_path / "download.php", "readme.txt")
    assert flaresolverr._name_by_content(saved, "example.apk") == saved
    assert saved.exists()


def test_non_zip_file_keeps_its_name(tmp_path):
    saved = tmp_path / "download.php"
    saved.write_bytes(b"not a zip")
    assert flaresolverr._name_by_content(saved, "example.apk") == saved
    assert saved.exists()


def test_already_correct_name_is_untouched(tmp_path):
    saved = _write_zip(tmp_path / "youtube.apk", "AndroidManifest.xml")
    assert flaresolverr._name_by_content(saved, "youtube.apk") == saved
    assert saved.exists()


def test_existing_target_is_replaced(tmp_path):
    (tmp_path / "youtube.apk").write_bytes(b"old")
    saved = _write_zip(tmp_path / "download.php", "AndroidManifest.xml")
    result = flaresolverr._name_by_content(saved, "youtube.apk")
    assert zipfile.is_zipfile(result)


class _FakeStream:
    def __init__(self, payload, *, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def aiter_content(self):
        yield self._payload


class _FakeClient:
    """Hands back one _FakeStream per .stream() call, pulled from
    `responses` in order (repeating the last one once exhausted) - lets a
    test simulate an attempt failing and a later retry succeeding."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method, url, headers=None, cookies=None):
        index = min(self.call_count, len(self._responses) - 1)
        stream = self._responses[index]
        self.call_count += 1
        return stream


async def test_download_file_saves_bundle_with_apkm_extension(tmp_path, monkeypatch):
    payload = _zip_bytes_realistic("info.json", "base.apk")
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: _FakeClient(_FakeStream(payload)))
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    result = await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "reddit.apk")
    assert result == tmp_path / "reddit.apkm"
    assert zipfile.is_zipfile(result)
    assert not list(tmp_path.glob("*.part"))


async def test_download_file_saves_plain_apk_with_apk_extension(tmp_path, monkeypatch):
    payload = _zip_bytes_realistic("AndroidManifest.xml", "classes.dex")
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: _FakeClient(_FakeStream(payload)))
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    result = await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "youtube.apk")
    assert result == tmp_path / "youtube.apk"
    assert zipfile.is_zipfile(result)


async def test_download_file_verifies_content_length(tmp_path, monkeypatch):
    payload = _zip_bytes_realistic("AndroidManifest.xml")
    headers = {"content-length": str(len(payload) + 5000)}  # server claims more than it actually sends
    monkeypatch.setattr(
        flaresolverr, "new_session", lambda **kwargs: _FakeClient(_FakeStream(payload, headers=headers))
    )
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    try:
        await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "youtube.apk")
        raise AssertionError("expected a FlareSolverrError for the size mismatch")
    except flaresolverr.FlareSolverrError as e:
        assert "incomplete" in str(e).lower()
    assert not list(tmp_path.glob("*.part")), "a failed download must not leave a .part file behind"


async def test_download_file_rejects_too_small_payload(tmp_path, monkeypatch):
    payload = _zip_bytes("AndroidManifest.xml")  # the tiny, un-padded fixture: well under 1024 bytes
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: _FakeClient(_FakeStream(payload)))
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    try:
        await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "youtube.apk")
        raise AssertionError("expected a FlareSolverrError for the too-small payload")
    except flaresolverr.FlareSolverrError as e:
        assert "small" in str(e).lower()
    assert not list(tmp_path.glob("*.part"))


async def test_download_file_rejects_non_zip_payload(tmp_path, monkeypatch):
    payload = b"<html><body>captcha / error page, not an apk</body></html>" * 30
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: _FakeClient(_FakeStream(payload)))
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    try:
        await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "youtube.apk")
        raise AssertionError("expected a FlareSolverrError for the non-archive payload")
    except flaresolverr.FlareSolverrError as e:
        assert "archive" in str(e).lower()
    assert not list(tmp_path.glob("*.part"))


async def test_download_file_retries_once_then_succeeds(tmp_path, monkeypatch):
    bad_payload = b"too small"
    good_payload = _zip_bytes_realistic("AndroidManifest.xml")
    client = _FakeClient(_FakeStream(bad_payload), _FakeStream(good_payload))
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: client)
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    result = await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "youtube.apk")
    assert result == tmp_path / "youtube.apk"
    assert client.call_count == 2
