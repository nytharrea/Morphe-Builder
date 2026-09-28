"""Unit tests for build fingerprint + plan models (no network)."""

from morphe_builder.build.fingerprint import config_fingerprint
from morphe_builder.build.models import BuildDecision, BuildPlan, BuildRecord, Manifest
from morphe_builder.catalog import BUILDS


def test_config_fingerprint_stable():
    key = next(iter(BUILDS))
    a = config_fingerprint(BUILDS[key])
    b = config_fingerprint(BUILDS[key])
    assert a == b
    assert len(a) == 16


def test_config_fingerprint_changes_with_options():
    key = next(iter(BUILDS))
    base = dict(BUILDS[key])
    h1 = config_fingerprint(base)  # type: ignore[arg-type]
    changed = dict(base)
    opts = dict(changed.get("options") or {})
    opts["__test__"] = "x"
    changed["options"] = opts
    h2 = config_fingerprint(changed)  # type: ignore[arg-type]
    assert h1 != h2


def test_manifest_roundtrip():
    rec = BuildRecord(
        build_key="youtube",
        app_version="20.5",
        patches={"morphe": "v1.5"},
        config_hash="abc",
        apk_name="YouTube-20.5.apk",
        display_name="YouTube",
    )
    m = Manifest(release_tag="build-1", builds={"youtube": rec})
    m2 = Manifest.from_dict(m.to_dict())
    assert m2.builds["youtube"].app_version == "20.5"
    assert m2.builds["youtube"].patches["morphe"] == "v1.5"


def test_plan_to_build_to_skip():
    d_build = BuildDecision(
        build_key="youtube",
        decision="build",
        reason="app update",
        app_version="20.6",
        previous_app_version="20.5",
        patches={"morphe": "v1.5"},
        previous_patches={"morphe": "v1.5"},
        config_hash="h1",
        previous_config_hash="h1",
        previous_apk_name="YouTube-20.5.apk",
        display_name="YouTube",
    )
    d_skip = BuildDecision(
        build_key="reddit",
        decision="skip",
        reason="unchanged",
        app_version="2026.09",
        previous_app_version="2026.09",
        patches={"morphe": "v1.5"},
        previous_patches={"morphe": "v1.5"},
        config_hash="h2",
        previous_config_hash="h2",
        previous_apk_name="Reddit-2026.09.apk",
        display_name="Reddit",
    )
    plan = BuildPlan(release_tag="t", release_name="n", decisions=[d_build, d_skip])
    assert plan.to_build == ["youtube"]
    assert plan.to_skip == ["reddit"]
    plan2 = BuildPlan.from_dict(plan.to_dict())
    assert plan2.to_build == ["youtube"]
