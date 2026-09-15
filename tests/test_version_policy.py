from morphe_builder.version_policy import pick_version, version_core_tuple


def test_version_core_tuple_numeric():
    assert version_core_tuple("12.25.0-prod.01") == (12, 25, 0)


def test_version_core_tuple_unparseable():
    assert version_core_tuple("abc") == (0,)


def test_pick_version_default_prefers_patch_count():
    versions = [{"version": "1.2.2", "patches": 10}, {"version": "1.2.4", "patches": 5}]
    assert pick_version(versions) == "1.2.2"


def test_pick_version_latest_compatible_prefers_version_then_patches():
    versions = [{"version": "1.2.2", "patches": 10}, {"version": "1.2.10", "patches": 5}]
    assert pick_version(versions, "latest_compatible") == "1.2.10"


def test_pick_version_empty():
    assert pick_version([]) is None
