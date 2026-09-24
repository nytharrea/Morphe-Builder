"""validate_catalog() only has BUILDS/PATCH_SOURCES left to cross-check now
(see morphe_builder/validate.py's docstring for why the old
PROCESS_ORDER<->workflow-matrix check and the apkmirror.APP_SITES
cross-reference check don't have a new equivalent here: both classes of
drift are structurally impossible now, not just detected - the workflow
matrix is generated from this same catalog at run time, and apk_source
lives right on the app entry instead of in a separate table it could fall
out of sync with)."""

import pytest

from morphe_builder import validate


def test_real_catalog_passes_validation():
    validate.validate_catalog()


def _youtube_build():
    return dict(validate.BUILDS["youtube"])


def test_unknown_patch_source_is_caught(monkeypatch):
    monkeypatch.setitem(
        validate.BUILDS, "youtube", {**_youtube_build(), "patch_sources": ["totally-made-up-source"]}
    )
    with pytest.raises(validate.ConfigError, match="totally-made-up-source"):
        validate.validate_catalog()


def test_build_with_no_patch_sources_is_caught(monkeypatch):
    monkeypatch.setitem(validate.BUILDS, "youtube", {**_youtube_build(), "patch_sources": []})
    with pytest.raises(validate.ConfigError, match="no patch_sources"):
        validate.validate_catalog()


def test_exclude_as_string_instead_of_list_is_caught(monkeypatch):
    monkeypatch.setitem(validate.BUILDS, "youtube", {**_youtube_build(), "exclude": "Dynamic color"})
    with pytest.raises(validate.ConfigError, match="should be a list"):
        validate.validate_catalog()


def test_enable_as_string_instead_of_list_is_caught(monkeypatch):
    monkeypatch.setitem(validate.BUILDS, "youtube", {**_youtube_build(), "enable": "Clone app"})
    with pytest.raises(validate.ConfigError, match="should be a list"):
        validate.validate_catalog()


def test_multiple_problems_are_all_reported_together(monkeypatch):
    monkeypatch.setitem(
        validate.BUILDS,
        "youtube",
        {**_youtube_build(), "patch_sources": ["made-up"], "exclude": "not-a-list"},
    )
    with pytest.raises(validate.ConfigError) as exc_info:
        validate.validate_catalog()
    message = str(exc_info.value)
    assert "made-up" in message
    assert "should be a list" in message


def test_apkmirror_source_missing_a_required_field_is_caught(monkeypatch):
    build = _youtube_build()
    # youtube's apk_source is {"type": "apkmirror", "org": ..., "slug": ...} -
    # copy it before deleting a key, rather than mutating the dict BUILDS
    # itself still holds a reference to.
    broken_source = {**build["apk_source"]}
    del broken_source["slug"]
    monkeypatch.setitem(validate.BUILDS, "youtube", {**build, "apk_source": broken_source})
    with pytest.raises(validate.ConfigError, match='apkmirror.*no "slug"'):
        validate.validate_catalog()


def test_github_source_missing_required_fields_is_caught(monkeypatch):
    monkeypatch.setitem(validate.BUILDS, "youtube", {**_youtube_build(), "apk_source": {"type": "github"}})
    with pytest.raises(validate.ConfigError) as exc_info:
        validate.validate_catalog()
    message = str(exc_info.value)
    assert 'no "owner"' in message
    assert 'no "repo"' in message


def test_apk_source_with_no_recognized_type_has_no_field_requirements(monkeypatch):
    """Not this function's job: catalog.py's loader already rejects an
    apk_source.type that isn't "apkmirror" or "github" before
    validate_catalog() ever runs, so an unrecognized type reaching here at
    all shouldn't be possible - this just documents that this check stays
    silent about it either way, rather than guessing what fields it would
    have needed."""
    monkeypatch.setitem(validate.BUILDS, "youtube", {**_youtube_build(), "apk_source": {"type": "carrier-pigeon"}})
    validate.validate_catalog()
