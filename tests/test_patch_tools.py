import pytest

from core import patch_tools


def _release(tag, *assets, draft=False):
    return {"tag_name": tag, "draft": draft, "assets": [{"name": name} for name in assets]}


def _is_patch_bundle(name):
    return name.endswith(".mpp")


def _releases():
    return [
        _release("theme-previews-v1", "theme-previews-v1.zip", "theme-previews-v1-manifest.json"),
        _release("v3.10.0", "patches-3.10.0.mpp"),
        _release("v3.9.0", "patches-3.9.0.mpp"),
    ]


def test_select_release_skips_newest_release_without_patch_bundle():
    release = patch_tools._select_release(_releases(), _is_patch_bundle)
    assert release is not None
    assert release["tag_name"] == "v3.10.0"


def test_select_release_prefers_newest_release_that_has_a_patch_bundle():
    releases = [_release("v4.0.0-dev.1", "patches-4.0.0-dev.1.mpp"), *_releases()]
    release = patch_tools._select_release(releases, _is_patch_bundle)
    assert release is not None
    assert release["tag_name"] == "v4.0.0-dev.1"


def test_select_release_skips_drafts():
    releases = [_release("v9.9.9", "patches-9.9.9.mpp", draft=True), *_releases()]
    release = patch_tools._select_release(releases, _is_patch_bundle)
    assert release is not None
    assert release["tag_name"] == "v3.10.0"


def test_select_release_returns_none_when_nothing_matches():
    assert patch_tools._select_release(_releases()[:1], _is_patch_bundle) is None


def test_select_release_without_matcher_returns_first_published_release():
    releases = [_release("v9.9.9", draft=True), *_releases()]
    release = patch_tools._select_release(releases)
    assert release is not None
    assert release["tag_name"] == "theme-previews-v1"


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        return _FakeResponse(self._payload)


async def test_fetch_latest_release_prerelease_returns_release_with_patch_bundle(monkeypatch):
    monkeypatch.setattr(patch_tools, "new_session", lambda **kwargs: _FakeSession(_releases()))
    release = await patch_tools.fetch_latest_release("owner", "repo", True, _is_patch_bundle)
    assert release["tag_name"] == "v3.10.0"


async def test_fetch_latest_release_prerelease_raises_when_no_release_has_patch_bundle(monkeypatch):
    monkeypatch.setattr(patch_tools, "new_session", lambda **kwargs: _FakeSession(_releases()[:1]))
    with pytest.raises(RuntimeError, match="owner/repo"):
        await patch_tools.fetch_latest_release("owner", "repo", True, _is_patch_bundle)
