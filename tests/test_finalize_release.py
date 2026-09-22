from scripts.finalize_release import find_patched_apks, match_asset


def test_match_asset_simple_app():
    assert match_asset("YouTube-19.35.36.apk") == ("youtube", "YouTube", "19.35.36")


def test_match_asset_prefix_collision_resolved_by_longest_match_first():
    assert match_asset("Reddit-Adobo-2024.15.0.apk") == (
        "reddit-adobo",
        "Reddit-Adobo",
        "2024.15.0",
    )


def test_match_asset_plain_reddit_still_resolves_correctly():
    assert match_asset("Reddit-2024.15.0.apk") == ("reddit", "Reddit", "2024.15.0")


def test_match_asset_disambiguates_same_display_name_by_patch_source_owner():
    # All five tiktok-* builds share the app-level display_name "TikTok", so
    # each one's filename is disambiguated with its primary patch source's
    # GitHub owner - this is the exact mechanism that lets the same app be
    # patched from several different sources without the outputs colliding.
    assert match_asset("TikTok-46.2.3-icysymmetra.apk") == ("tiktok", "TikTok", "46.2.3")
    assert match_asset("TikTok-46.2.3-hxreborn.apk") == ("tiktok-hxreborn", "TikTok", "46.2.3")
    assert match_asset("TikTok-46.4.3-BlueDragon4251.apk") == ("tiktok-bluedragon", "TikTok", "46.4.3")


def test_match_asset_ignores_microg():
    assert match_asset("MicroG-25.09.32.apk") is None


def test_match_asset_ignores_pothelper():
    assert match_asset("PotHelper-1.1.1.apk") is None


def test_match_asset_unknown_app_returns_none():
    assert match_asset("SomeRandomApp-1.0.apk") is None


def test_match_asset_non_apk_file_returns_none():
    assert match_asset("readme.txt") is None


def test_find_patched_apks_splits_matched_and_unmatched(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "youtube").mkdir(parents=True)
    (artifacts_dir / "reddit").mkdir(parents=True)
    (artifacts_dir / "unknown").mkdir(parents=True)

    (artifacts_dir / "youtube" / "YouTube-19.35.36.apk").write_bytes(b"fake apk")
    (artifacts_dir / "reddit" / "Reddit-2024.15.0.apk").write_bytes(b"fake apk")
    (artifacts_dir / "unknown" / "Something-1.0.apk").write_bytes(b"fake apk")
    (artifacts_dir / "notes.txt").write_bytes(b"not an apk")

    matched, unmatched = find_patched_apks(artifacts_dir)

    matched_keys = {m["build_key"] for m in matched}
    assert matched_keys == {"youtube", "reddit"}
    assert unmatched == ["Something-1.0.apk"]

    youtube_entry = next(m for m in matched if m["build_key"] == "youtube")
    assert youtube_entry["version"] == "19.35.36"
    assert youtube_entry["display_name"] == "YouTube"


def test_find_patched_apks_empty_dir(tmp_path):
    artifacts_dir = tmp_path / "empty"
    artifacts_dir.mkdir()
    matched, unmatched = find_patched_apks(artifacts_dir)
    assert matched == []
    assert unmatched == []
