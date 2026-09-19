import io
import zipfile

from core.sources import flaresolverr

DOWNLOAD_URL = "https://www.apkmirror.com/wp-content/themes/APKMirror/download.php?id=1&key=2"


def _zip_bytes(*names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
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
    status_code = 200
    headers: dict = {}

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def aiter_content(self):
        yield self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method, url, headers=None, cookies=None):
        return _FakeStream(self._payload)


async def test_download_file_saves_bundle_with_apkm_extension(tmp_path, monkeypatch):
    payload = _zip_bytes("info.json", "base.apk")
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: _FakeClient(payload))
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    result = await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "reddit.apk")
    assert result == tmp_path / "reddit.apkm"
    assert zipfile.is_zipfile(result)


async def test_download_file_saves_plain_apk_with_apk_extension(tmp_path, monkeypatch):
    payload = _zip_bytes("AndroidManifest.xml", "classes.dex")
    monkeypatch.setattr(flaresolverr, "new_session", lambda **kwargs: _FakeClient(payload))
    cleared = flaresolverr.Cleared("https://www.apkmirror.com/", 200, "", "ua", [])
    result = await flaresolverr.download_file(DOWNLOAD_URL, cleared, tmp_path, "youtube.apk")
    assert result == tmp_path / "youtube.apk"
    assert zipfile.is_zipfile(result)
