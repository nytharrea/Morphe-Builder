from morphe_builder.fetchers import github_app


def test_build_tag_formats_template_when_version_lacks_the_prefix():
    assert github_app._build_tag("build{version}", "123") == "build123"


def test_build_tag_passes_version_through_when_already_prefixed():
    assert github_app._build_tag("build{version}", "build123") == "build123"


def test_build_tag_with_no_prefix_template():
    assert github_app._build_tag("{version}", "7.4.0") == "7.4.0"


def test_pick_apk_asset_prefers_hinted_name():
    assets = [
        {"name": "Inure-play-arm64-v8a.apk", "size": 100},
        {"name": "Inure-github-arm64-v8a.apk", "size": 100},
    ]
    assert github_app._pick_apk_asset(assets, "github")["name"] == "Inure-github-arm64-v8a.apk"


def test_pick_apk_asset_prefers_arm64_among_hinted_candidates():
    assets = [
        {"name": "Inure-github-armeabi-v7a.apk", "size": 90},
        {"name": "Inure-github-arm64-v8a.apk", "size": 100},
        {"name": "Inure-play-arm64-v8a.apk", "size": 100},
    ]
    assert github_app._pick_apk_asset(assets, "github")["name"] == "Inure-github-arm64-v8a.apk"


def test_pick_apk_asset_falls_back_to_unhinted_when_hint_matches_nothing():
    assets = [{"name": "SomeOtherApp-arm64-v8a.apk", "size": 100}]
    assert github_app._pick_apk_asset(assets, "github")["name"] == "SomeOtherApp-arm64-v8a.apk"


def test_pick_apk_asset_ignores_non_apk_assets():
    assets = [{"name": "source.zip", "size": 10}, {"name": "checksums.txt", "size": 1}]
    assert github_app._pick_apk_asset(assets) is None


def test_pick_apk_asset_accepts_apkm_extension():
    assets = [{"name": "bundle.apkm", "size": 500}]
    assert github_app._pick_apk_asset(assets)["name"] == "bundle.apkm"


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        return self._responses.pop(0)


SOURCE = {"owner": "Hamza417", "repo": "Inure", "asset_hint": "github", "tag_template": "build{version}"}


async def test_download_apk_uses_the_matching_tag_when_found(monkeypatch, tmp_path):
    tag_response = _FakeResponse(
        200,
        {"assets": [{"name": "Inure-github-arm64-v8a.apk", "size": 2000, "browser_download_url": "https://x"}]},
    )
    monkeypatch.setattr(github_app, "new_session", lambda **kw: _FakeSession([tag_response]))

    async def fake_download_asset(client, asset):
        return str(tmp_path / asset["name"])

    monkeypatch.setattr(github_app, "_download_asset", fake_download_asset)

    result = await github_app.download_apk("123", "inure-github", SOURCE)
    assert result == str(tmp_path / "Inure-github-arm64-v8a.apk")


async def test_download_apk_falls_back_to_latest_when_tag_not_found(monkeypatch, tmp_path):
    tag_missing = _FakeResponse(404)
    latest_ok = _FakeResponse(
        200,
        {"assets": [{"name": "Inure-github-arm64-v8a.apk", "size": 2000, "browser_download_url": "https://x"}]},
    )
    monkeypatch.setattr(github_app, "new_session", lambda **kw: _FakeSession([tag_missing, latest_ok]))

    async def fake_download_asset(client, asset):
        return "downloaded"

    monkeypatch.setattr(github_app, "_download_asset", fake_download_asset)

    result = await github_app.download_apk("999", "inure-github", SOURCE)
    assert result == "downloaded"


async def test_download_apk_raises_when_latest_release_api_call_fails(monkeypatch):
    monkeypatch.setattr(github_app, "new_session", lambda **kw: _FakeSession([_FakeResponse(500)]))
    try:
        await github_app.download_apk("latest", "inure-github", SOURCE)
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "GitHub API error" in str(e)


async def test_download_apk_raises_when_no_matching_asset_in_release(monkeypatch):
    empty_release = _FakeResponse(200, {"assets": [{"name": "source.zip", "size": 10}]})
    monkeypatch.setattr(github_app, "new_session", lambda **kw: _FakeSession([empty_release]))
    try:
        await github_app.download_apk("latest", "inure-github", SOURCE)
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "No .apk or .apkm file found" in str(e)


async def test_download_apk_accepts_but_ignores_force_build(monkeypatch, tmp_path):
    # force_build has no effect for a GitHub-hosted app (no "variant" concept
    # the way an APKMirror release page has) - it's accepted purely so
    # scripts/patch.py can call apkmirror.download_apk and this function
    # with the same signature. This just confirms passing it doesn't break
    # anything or change the outcome.
    release = _FakeResponse(
        200,
        {"assets": [{"name": "Inure-github-arm64-v8a.apk", "size": 2000, "browser_download_url": "https://x"}]},
    )
    monkeypatch.setattr(github_app, "new_session", lambda **kw: _FakeSession([release]))

    async def fake_download_asset(client, asset):
        return "ok"

    monkeypatch.setattr(github_app, "_download_asset", fake_download_asset)

    result = await github_app.download_apk("latest", "inure-github", SOURCE, force_build="some-build-id")
    assert result == "ok"


async def test_get_latest_listing_builds_releases_url():
    result = await github_app.get_latest_listing("inure-github", SOURCE)
    assert result == {"version": "latest", "href": "https://github.com/Hamza417/Inure/releases/latest"}
