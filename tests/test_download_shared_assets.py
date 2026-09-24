"""Covers download_shared_assets.py: it should fetch the desktop CLI jar
once, then exactly one .mpp per catalog patch source - the same set
every matrix job's own scripts/patch.py would otherwise have downloaded
independently."""

from morphe_builder import catalog
from scripts import download_shared_assets


async def test_downloads_desktop_jar_and_every_patch_source(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "PATCH_SOURCES",
        {
            "src_a": {"owner": "acme", "repo": "patches-a", "label": "A"},
            "src_b": {"owner": "other", "repo": "patches-b", "label": "B"},
        },
    )

    calls = []

    async def fake_download(owner, repo, prerelease, match):
        calls.append((owner, repo, prerelease))
        # Sanity-check the match predicates without hardcoding their exact
        # wording - just that a desktop call matches jars (not apks) and a
        # patch-source call matches .mpp (not apks).
        if owner == "MorpheApp":
            assert match("morphe-desktop-v1.jar") is True
            assert match("morphe-desktop-v1.apk") is False
        else:
            assert match("some-patches.mpp") is True
            assert match("some-patches.apk") is False
        return {"name": f"{owner}-{repo}.asset"}

    monkeypatch.setattr(download_shared_assets, "download_latest_release_asset", fake_download)

    await download_shared_assets.main()

    assert len(calls) == 3  # desktop jar + exactly one call per patch source, no duplicates
    assert ("MorpheApp", "morphe-desktop", True) in calls
    assert ("acme", "patches-a", True) in calls
    assert ("other", "patches-b", True) in calls


async def test_two_patch_sources_in_the_same_repo_both_still_get_a_call(monkeypatch):
    """download_latest_release_asset() itself already skips a redundant
    download when the target file is already on disk (that's what makes
    the whole shared-artifact approach work) - this just confirms this
    script doesn't try to be clever and dedupe by repo itself, which
    would risk skipping a genuinely different source sharing a repo."""
    monkeypatch.setattr(
        catalog,
        "PATCH_SOURCES",
        {
            "src_a": {"owner": "acme", "repo": "shared-repo", "label": "A"},
            "src_b": {"owner": "acme", "repo": "shared-repo", "label": "B"},
        },
    )

    calls = []

    async def fake_download(owner, repo, prerelease, match):
        calls.append((owner, repo))
        return {"name": "asset"}

    monkeypatch.setattr(download_shared_assets, "download_latest_release_asset", fake_download)

    await download_shared_assets.main()

    assert calls.count(("acme", "shared-repo")) == 2
