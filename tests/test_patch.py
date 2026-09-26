"""Covers process_build()'s apk_source.type branch - the exact logic the
scripts/patch.py mypy fix touches (branching on source["type"] directly
instead of through a separately-computed bool so mypy can narrow the
ApkMirrorSource | GithubAppSource union). Nothing previously exercised
this function at all, so this is also the first regression guard against
an apkmirror-type build ever calling the github_app fetcher, or
vice versa.
"""

from morphe_builder import catalog
from morphe_builder.fetchers import apkmirror, github_app
from scripts import patch


def _fake_build(app_slug: str, source: dict, force_version: str | None = "1.2.3") -> dict:
    return {
        "key": app_slug,
        "app_slug": app_slug,
        "display_name": app_slug.title(),
        "arch": "arm64-v8a",
        "icon": "https://example.com/icon.png",
        "apk_source": source,
        "patch_sources": ["some-source"],
        "exclude": [],
        "enable": [],
        "force_version": force_version,  # "1.2.3" skips list-versions/get_latest_listing entirely
        "force_build": None,
    }


def _common_mocks(monkeypatch, tmp_path, apk_source: dict, force_version: str | None = "1.2.3"):
    """Stub out everything on either side of the type branch we actually
    want to exercise: with the default force_version, version resolution
    is skipped entirely, and verify/patch/copy are faked out with a real
    file on disk so process_build's own Path.exists()/shutil.copyfile
    calls succeed."""
    build_key = "the-app"
    monkeypatch.setattr(catalog, "BUILDS", {build_key: _fake_build(build_key, apk_source, force_version)})
    monkeypatch.setattr(
        catalog, "PATCH_SOURCES", {"some-source": {"owner": "acme", "repo": "patches", "label": "x"}}
    )
    monkeypatch.setattr(patch.catalog, "get_release_naming", lambda key: ("The App", None))

    monkeypatch.setattr(patch, "verify_apk_signature", lambda apk_path, app_slug: None)

    patched_apk = tmp_path / "patched.apk"
    patched_apk.write_bytes(b"fake patched apk")
    monkeypatch.setattr(patch, "patch_apk", lambda *a, **kw: str(patched_apk))
    monkeypatch.setattr(patch, "DIST_DIR", tmp_path / "dist")

    return build_key


async def test_process_build_calls_apkmirror_fetcher_for_an_apkmirror_source(monkeypatch, tmp_path):
    apk_source = {"type": "apkmirror", "org": "acme-inc", "slug": "acme-app"}
    build_key = _common_mocks(monkeypatch, tmp_path, apk_source)

    calls = []

    async def fake_apkmirror_download(version, app_slug, source, force_build):
        calls.append(("apkmirror", version, app_slug, source))
        return str(tmp_path / "downloaded.apk")

    async def fake_github_download(*a, **kw):
        raise AssertionError("github_app.download_apk should not be called for an apkmirror source")

    monkeypatch.setattr(apkmirror, "download_apk", fake_apkmirror_download)
    monkeypatch.setattr(github_app, "download_apk", fake_github_download)

    result = await patch.process_build(build_key, "desktop.jar", ["patch1.mpp"])

    assert result is not None
    assert calls == [("apkmirror", "1.2.3", build_key, apk_source)]


async def test_process_build_calls_github_fetcher_for_a_github_source(monkeypatch, tmp_path):
    apk_source = {"type": "github", "owner": "acme-inc", "repo": "acme-app"}
    build_key = _common_mocks(monkeypatch, tmp_path, apk_source)

    calls = []

    async def fake_github_download(version, app_slug, source, force_build):
        calls.append(("github", version, app_slug, source))
        return str(tmp_path / "downloaded.apk")

    async def fake_apkmirror_download(*a, **kw):
        raise AssertionError("apkmirror.download_apk should not be called for a github source")

    monkeypatch.setattr(github_app, "download_apk", fake_github_download)
    monkeypatch.setattr(apkmirror, "download_apk", fake_apkmirror_download)

    result = await patch.process_build(build_key, "desktop.jar", ["patch1.mpp"])

    assert result is not None
    assert calls == [("github", "1.2.3", build_key, apk_source)]


async def test_process_build_returns_none_when_patch_apk_did_not_produce_a_file(monkeypatch, tmp_path):
    apk_source = {"type": "github", "owner": "acme-inc", "repo": "acme-app"}
    build_key = _common_mocks(monkeypatch, tmp_path, apk_source)

    async def fake_github_download(*a, **kw):
        return str(tmp_path / "downloaded.apk")

    monkeypatch.setattr(github_app, "download_apk", fake_github_download)
    # Overrides the real file _common_mocks() created - simulates patch_apk()
    # returning a path that doesn't actually exist.
    monkeypatch.setattr(patch, "patch_apk", lambda *a, **kw: str(tmp_path / "never-written.apk"))

    assert await patch.process_build(build_key, "desktop.jar", ["patch1.mpp"]) is None


async def test_process_build_falls_through_to_latest_for_a_github_source_with_no_version_data(
    monkeypatch, tmp_path
):
    """list-versions runs for every source type - patch-version
    compatibility isn't specific to where the APK is downloaded from.
    This covers the case where it finds nothing for this app (routine:
    many apps have no per-version compatibility data at all), which
    must fall through to "latest" rather than guessing."""
    apk_source = {"type": "github", "owner": "acme-inc", "repo": "acme-app"}
    build_key = _common_mocks(monkeypatch, tmp_path, apk_source, force_version=None)

    ran = []

    class _FakeCompleted:
        returncode = 0
        stdout = "Nothing useful here.\n"  # no "Most common compatible versions" section
        stderr = ""

    def fake_subprocess_run(*a, **kw):
        ran.append(True)
        return _FakeCompleted()

    monkeypatch.setattr(patch.subprocess, "run", fake_subprocess_run)

    calls = []

    async def fake_github_download(version, app_slug, source, force_build):
        calls.append(version)
        return str(tmp_path / "downloaded.apk")

    monkeypatch.setattr(github_app, "download_apk", fake_github_download)

    result = await patch.process_build(build_key, "desktop.jar", ["patch1.mpp"])

    assert result is not None
    assert ran == [True]  # list-versions DID run - unlike apkmirror, github isn't skipped
    assert calls == ["latest"]


async def test_process_build_prefers_a_patch_recommended_version_for_a_github_source(monkeypatch, tmp_path):
    """The actual point of this design: when the patches DO have a
    recorded compatible version for this app, that's what should get
    fetched - even for a github source, and even though a newer release
    exists upstream. Patch compatibility, not "newest", is what matters:
    a version the patches weren't written against can fail to apply
    cleanly, or apply but misbehave."""
    apk_source = {"type": "github", "owner": "acme-inc", "repo": "acme-app"}
    build_key = _common_mocks(monkeypatch, tmp_path, apk_source, force_version=None)

    class _FakeCompleted:
        returncode = 0
        stdout = "Most common compatible versions:\n9.9.9 (3 patches)\n\n"
        stderr = ""

    monkeypatch.setattr(patch.subprocess, "run", lambda *a, **kw: _FakeCompleted())

    calls = []

    async def fake_github_download(version, app_slug, source, force_build):
        calls.append(version)
        return str(tmp_path / "downloaded.apk")

    monkeypatch.setattr(github_app, "download_apk", fake_github_download)

    result = await patch.process_build(build_key, "desktop.jar", ["patch1.mpp"])

    assert result is not None
    assert calls == ["9.9.9"]  # the patch-recommended version, not "latest"


async def test_process_build_runs_list_versions_for_an_apkmirror_source(monkeypatch, tmp_path):
    apk_source = {"type": "apkmirror", "org": "acme-inc", "slug": "acme-app"}
    build_key = _common_mocks(monkeypatch, tmp_path, apk_source, force_version=None)

    ran = []

    class _FakeCompleted:
        returncode = 0
        stdout = "Most common compatible versions:\n9.9.9 (3 patches)\n\n"
        stderr = ""

    def fake_subprocess_run(*a, **kw):
        ran.append(True)
        return _FakeCompleted()

    monkeypatch.setattr(patch.subprocess, "run", fake_subprocess_run)

    calls = []

    async def fake_apkmirror_download(version, app_slug, source, force_build):
        calls.append(version)
        return str(tmp_path / "downloaded.apk")

    monkeypatch.setattr(apkmirror, "download_apk", fake_apkmirror_download)

    result = await patch.process_build(build_key, "desktop.jar", ["patch1.mpp"])

    assert result is not None
    assert ran == [True]
    assert calls == ["9.9.9"]
