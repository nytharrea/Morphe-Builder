import json
import re

import pytest

from morphe_builder import catalog
from scripts import prepare_release


def test_main_writes_tag_name_and_matrix_to_github_output(monkeypatch, tmp_path):
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    prepare_release.main()

    lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in output_file.read_text().splitlines()}
    assert set(lines) == {"tag", "name", "matrix"}
    assert re.match(r"^build-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$", lines["tag"])
    assert lines["name"].startswith("Patched APKs - ")

    matrix = json.loads(lines["matrix"])
    assert matrix == list(catalog.BUILDS)
    assert "youtube" in matrix
    assert "tiktok-hxreborn" in matrix


def test_main_does_not_crash_when_github_output_is_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    prepare_release.main()  # must not raise


def test_main_exits_before_writing_anything_when_catalog_is_invalid(monkeypatch, tmp_path):
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    def broken_validate():
        raise Exception("catalog is broken on purpose")

    monkeypatch.setattr(prepare_release, "validate_catalog", broken_validate)

    with pytest.raises(SystemExit) as exc_info:
        prepare_release.main()
    assert exc_info.value.code == 1
    assert not output_file.exists()
