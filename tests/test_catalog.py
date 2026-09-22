"""Tests for morphe_builder/catalog.py's core job: merging catalog/apps.yaml's
nested app -> [builds] shape into the flat, per-build BUILDS dict the rest
of the pipeline reads - the exact mechanism that lets reddit/reddit-adobo,
the five tiktok-* builds, and twitter/twitter-x each stay separate,
independently-patched builds while sharing one app entry's pkg/icon/arch/
apk_source instead of repeating them."""

import pytest
import yaml

from morphe_builder import catalog


def _write_catalog(tmp_path, apps: dict, patch_sources: dict | None = None):
    apps_path = tmp_path / "apps.yaml"
    sources_path = tmp_path / "patch_sources.yaml"
    apps_path.write_text(yaml.safe_dump(apps))
    sources_path.write_text(
        yaml.safe_dump(patch_sources or {"morphe": {"owner": "MorpheApp", "repo": "morphe-patches"}})
    )
    return apps_path, sources_path


def _load(monkeypatch, tmp_path, apps: dict, patch_sources: dict | None = None):
    apps_path, sources_path = _write_catalog(tmp_path, apps, patch_sources)
    monkeypatch.setattr(catalog.settings, "apps_catalog_path", apps_path)
    monkeypatch.setattr(catalog.settings, "patch_sources_catalog_path", sources_path)
    return catalog._load_patch_sources(), catalog._load_builds()


# ---- real catalog sanity checks --------------------------------------------


def test_reddit_has_two_builds_sharing_the_same_apk_source():
    reddit = catalog.BUILDS["reddit"]
    reddit_adobo = catalog.BUILDS["reddit-adobo"]

    assert reddit["app_slug"] == reddit_adobo["app_slug"] == "reddit"
    assert reddit["pkg"] == reddit_adobo["pkg"]
    assert reddit["apk_source"] == reddit_adobo["apk_source"]
    # but they're genuinely different builds, each with its own source(s)
    assert reddit["patch_sources"] != reddit_adobo["patch_sources"]


def test_all_five_tiktok_builds_share_app_level_fields():
    tiktok_keys = ["tiktok", "tiktok-hxreborn", "tiktok-bluedragon", "tiktok-hushfeed", "tiktok-kveld"]
    builds = [catalog.BUILDS[k] for k in tiktok_keys]

    assert all(b["app_slug"] == "tiktok" for b in builds)
    assert len({b["pkg"] for b in builds}) == 1
    assert len({b["icon"] for b in builds}) == 1
    assert len({b["display_name"] for b in builds}) == 1  # all inherit "TikTok"
    # but each has its own patch source(s) - that's the whole point
    assert len({tuple(b["patch_sources"]) for b in builds}) == 5


def test_speedtest_build_combines_multiple_patch_sources_into_one_build():
    speedtest = catalog.BUILDS["speedtest"]
    assert speedtest["patch_sources"] == ["rushi", "morphe"]


def test_patch_sources_for_matches_build_field():
    assert catalog.patch_sources_for("speedtest") == catalog.BUILDS["speedtest"]["patch_sources"]


# ---- get_release_naming: disambiguation ------------------------------------


def test_get_release_naming_no_collision_returns_no_suffix():
    assert catalog.get_release_naming("youtube") == ("YouTube", None)


def test_get_release_naming_disambiguates_colliding_tiktok_builds():
    name, suffix = catalog.get_release_naming("tiktok-hxreborn")
    assert name == "TikTok"
    assert suffix == catalog.PATCH_SOURCES["hxreborn-tiktok"]["owner"]


def test_get_release_naming_reddit_adobo_has_its_own_name_so_no_suffix_needed():
    assert catalog.get_release_naming("reddit-adobo") == ("Reddit-Adobo", None)


# ---- flatten/merge logic against isolated fixture catalogs -----------------


def _minimal_app(**overrides):
    app = {
        "pkg": "com.example.app",
        "display_name": "Example",
        "arch": "arm64-v8a",
        "icon": "https://example.com/icon.png",
        "apk_source": {"type": "apkmirror", "org": "example-org", "slug": "example"},
        "builds": [{"key": "example", "patch_sources": ["morphe"]}],
    }
    app.update(overrides)
    return app


def test_build_inherits_app_level_fields_when_not_overridden(monkeypatch, tmp_path):
    _, builds = _load(monkeypatch, tmp_path, {"myapp": _minimal_app()})
    build = builds["example"]
    assert build["pkg"] == "com.example.app"
    assert build["display_name"] == "Example"
    assert build["arch"] == "arm64-v8a"
    assert build["app_slug"] == "myapp"


def test_build_level_display_name_overrides_app_level(monkeypatch, tmp_path):
    app = _minimal_app(
        builds=[{"key": "example-variant", "patch_sources": ["morphe"], "display_name": "Custom Name"}]
    )
    _, builds = _load(monkeypatch, tmp_path, {"myapp": app})
    assert builds["example-variant"]["display_name"] == "Custom Name"


def test_multiple_builds_under_one_app_each_get_their_own_patch_sources(monkeypatch, tmp_path):
    app = _minimal_app(
        builds=[
            {"key": "myapp", "patch_sources": ["morphe"]},
            {"key": "myapp-alt", "patch_sources": ["adobo"]},
        ]
    )
    _, builds = _load(
        monkeypatch,
        tmp_path,
        {"myapp": app},
        patch_sources={
            "morphe": {"owner": "MorpheApp", "repo": "morphe-patches"},
            "adobo": {"owner": "jkennethcarino", "repo": "adobo"},
        },
    )
    assert set(builds) == {"myapp", "myapp-alt"}
    assert builds["myapp"]["app_slug"] == builds["myapp-alt"]["app_slug"] == "myapp"
    assert builds["myapp"]["patch_sources"] == ["morphe"]
    assert builds["myapp-alt"]["patch_sources"] == ["adobo"]


def test_exclude_and_enable_default_to_empty_list_when_absent(monkeypatch, tmp_path):
    _, builds = _load(monkeypatch, tmp_path, {"myapp": _minimal_app()})
    assert builds["example"]["exclude"] == []
    assert builds["example"]["enable"] == []


def test_force_version_and_force_build_default_to_none(monkeypatch, tmp_path):
    _, builds = _load(monkeypatch, tmp_path, {"myapp": _minimal_app()})
    assert builds["example"]["force_version"] is None
    assert builds["example"]["force_build"] is None


def test_github_apk_source_is_preserved_as_is(monkeypatch, tmp_path):
    app = _minimal_app(apk_source={"type": "github", "owner": "SomeOrg", "repo": "some-repo"})
    _, builds = _load(monkeypatch, tmp_path, {"myapp": app})
    assert builds["example"]["apk_source"] == {"type": "github", "owner": "SomeOrg", "repo": "some-repo"}


def test_duplicate_build_key_across_different_apps_raises(monkeypatch, tmp_path):
    apps = {
        "app-one": _minimal_app(builds=[{"key": "shared-key", "patch_sources": ["morphe"]}]),
        "app-two": _minimal_app(builds=[{"key": "shared-key", "patch_sources": ["morphe"]}]),
    }
    with pytest.raises(catalog.CatalogError, match="shared-key"):
        _load(monkeypatch, tmp_path, apps)


def test_build_with_no_patch_sources_raises_at_load_time(monkeypatch, tmp_path):
    app = _minimal_app(builds=[{"key": "example", "patch_sources": []}])
    with pytest.raises(catalog.CatalogError, match="no patch_sources"):
        _load(monkeypatch, tmp_path, {"myapp": app})


def test_app_missing_a_required_field_raises_a_clear_error(monkeypatch, tmp_path):
    app = _minimal_app()
    del app["icon"]
    with pytest.raises(catalog.CatalogError, match="icon"):
        _load(monkeypatch, tmp_path, {"myapp": app})


def test_patch_source_missing_required_field_raises_a_clear_error(monkeypatch, tmp_path):
    with pytest.raises(catalog.CatalogError, match="repo"):
        _load(monkeypatch, tmp_path, {"myapp": _minimal_app()}, patch_sources={"morphe": {"owner": "MorpheApp"}})


def test_non_mapping_yaml_file_raises_a_clear_error(monkeypatch, tmp_path):
    apps_path = tmp_path / "apps.yaml"
    sources_path = tmp_path / "patch_sources.yaml"
    apps_path.write_text("- just\n- a\n- list\n")
    sources_path.write_text("{}")
    monkeypatch.setattr(catalog.settings, "apps_catalog_path", apps_path)
    monkeypatch.setattr(catalog.settings, "patch_sources_catalog_path", sources_path)
    with pytest.raises(catalog.CatalogError, match="mapping"):
        catalog._load_builds()
