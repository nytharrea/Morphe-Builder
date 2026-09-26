from morphe_builder.apk import versions as versions_module
from morphe_builder.apk.versions import extract_cli_versions, pick_latest_version, to_apkmirror_version


def test_extract_versions_from_compatible_section():
    output = (
        "Some preamble text from the CLI\n"
        "Most common compatible versions:\n"
        "19.35.36 (5 patches)\n"
        "19.34.42 (3 patches)\n"
        "\n"
        "Trailing text that should not be reached\n"
    )
    result = extract_cli_versions(output)
    assert result == [
        {"version": "19.35.36", "patches": 5},
        {"version": "19.34.42", "patches": 3},
    ]


def test_extract_versions_ignores_non_matching_lines_in_section():
    output = "Most common compatible versions:\nsome unrelated note\n19.35.36 (5 patches)\n\n"
    result = extract_cli_versions(output)
    assert result == [{"version": "19.35.36", "patches": 5}]


def test_extract_versions_returns_empty_when_no_section_header():
    """Routine, not a failure: many apps (confirmed against real CI runs -
    roughly half of this catalog's apps hit this every run) have no
    per-version patch-compatibility data, so the CLI never prints the
    section at all. No fallback guess - the caller falls through to its
    own real latest-version lookup instead, which is more trustworthy
    than a version-looking number pulled from unrelated CLI text."""
    output = "App: com.example.app\nLatest available version: 439.0.0.37.89\n"
    assert extract_cli_versions(output) == []


def test_extract_versions_returns_empty_when_nothing_version_looking_is_present():
    assert extract_cli_versions("Nothing version-looking in here at all.") == []


def test_no_section_header_logs_nothing(monkeypatch):
    warnings = []
    monkeypatch.setattr(versions_module.log, "warn", warnings.append)

    extract_cli_versions("Available versions: 1.2.3, 1.2.4 and 2.0.0-beta.1 are supported")

    assert warnings == []


def test_section_header_present_but_unparseable_logs_warn_and_returns_empty(monkeypatch, tmp_path):
    """Unlike the above, the header IS present here - it's the per-line
    format under it that doesn't match, which is a genuine CLI output
    format mismatch worth flagging loudly. Still returns empty rather
    than guessing, same as the no-header case - the caller's own
    latest-version lookup is the fallback either way. Also saves the raw
    output as a diagnostic, so a future occurrence has actual text to fix
    the regex from instead of another guess."""
    warnings = []
    monkeypatch.setattr(versions_module.log, "warn", warnings.append)
    monkeypatch.setattr(versions_module.paths, "diagnostics_dir", lambda: tmp_path)

    raw_output = "Most common compatible versions:\n* v19.35.36 - 5 patches\n\n"
    result = extract_cli_versions(raw_output, app_slug="instagram")

    assert result == []
    assert len(warnings) == 1
    dumped = list(tmp_path.glob("list-versions-instagram-*.txt"))
    assert len(dumped) == 1
    assert dumped[0].read_text() == raw_output


def test_extract_versions_empty_input():
    assert extract_cli_versions("") == []


def test_pick_latest_version_prefers_higher_patch_count():
    versions = [
        {"version": "1.2.2", "patches": 10},
        {"version": "1.2.4", "patches": 5},
    ]
    assert pick_latest_version(versions) == "1.2.2"


def test_pick_latest_version_breaks_ties_numerically_not_lexically():
    versions = [
        {"version": "1.2.3", "patches": 5},
        {"version": "1.2.10", "patches": 5},
    ]
    assert pick_latest_version(versions) == "1.2.10"


def test_pick_latest_version_empty_list():
    assert pick_latest_version([]) is None


def test_pick_latest_version_handles_unparseable_version_gracefully():
    versions = [{"version": "abc", "patches": 1}]
    assert pick_latest_version(versions) == "abc"


def test_to_apkmirror_version_replaces_dots_with_dashes():
    assert to_apkmirror_version("19.35.36") == "19-35-36"


def test_to_apkmirror_version_no_dots():
    assert to_apkmirror_version("19") == "19"
