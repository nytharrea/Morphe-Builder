"""Covers commit_signature.py's build_key -> app_slug resolution: per its
own module docstring, signatures/*.json are keyed by *app slug*, but a
matrix job is invoked with its *build* key, and for any build whose key
differs from its app's slug (every multi-source build, e.g.
"reddit-adobo" -> "reddit") looking the build key up directly in those
files always finds nothing. Nothing previously exercised this script at
all, so this is also the first regression guard for that resolution.

git itself is never actually invoked here - `run()` is replaced with a
fake that just records calls (and, for "git reset --hard", simulates
origin/main not yet having today's value, the common case), so these
tests exercise exactly the pure-Python key-resolution and
diff-then-write logic without needing a real git repo.
"""

import json
import sys
from types import SimpleNamespace

from morphe_builder import catalog
from scripts import commit_signature


def _fake_build(key: str, app_slug: str) -> dict:
    return {
        "key": key,
        "app_slug": app_slug,
        "display_name": key,
        "arch": "arm64-v8a",
        "icon": "",
        "apk_source": {"type": "apkmirror", "org": "x", "slug": "x"},
        "patch_sources": ["x"],
        "exclude": [],
        "enable": [],
        "force_version": None,
        "force_build": None,
    }


def _fake_run(git_calls, reset_files=None):
    """reset_files: {path: content-to-write-on-`git reset --hard`},
    simulating what's actually on origin/main - separate from whatever
    the test put on disk first to represent verify.py's local-only edit.
    """

    def run(cmd):
        git_calls.append(cmd)
        if cmd[:3] == ["git", "reset", "--hard"] and reset_files:
            for path, content in reset_files.items():
                path.write_text(content)
        return SimpleNamespace(returncode=0, stderr="")

    return run


def test_main_resolves_build_key_to_app_slug_before_touching_signature_files(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "BUILDS", {"reddit-adobo": _fake_build("reddit-adobo", "reddit")})
    monkeypatch.setattr(sys, "argv", ["commit_signature.py", "reddit-adobo"])

    known_path = tmp_path / "known_signatures.json"
    pending_path = tmp_path / "pending_signatures.json"
    known_path.write_text("{}")
    # What a real run would have on disk right after verify.py: a new
    # value, keyed by app slug ("reddit") - verify.py never writes by
    # build key.
    pending_path.write_text(json.dumps({"reddit": "abc123fingerprint"}))
    monkeypatch.setattr(commit_signature, "FILES", [known_path, pending_path])

    git_calls = []
    # Simulate origin/main not having this value yet (the common case:
    # this is the first job to see it this run).
    monkeypatch.setattr(commit_signature, "run", _fake_run(git_calls, reset_files={pending_path: "{}"}))

    commit_signature.main()

    assert json.loads(pending_path.read_text()) == {"reddit": "abc123fingerprint"}
    push_calls = [c for c in git_calls if c[:2] == ["git", "push"]]
    assert push_calls, "should have committed and pushed the app-slug-keyed value it found"
    commit_calls = [c for c in git_calls if c[:2] == ["git", "commit"]]
    assert "reddit" in commit_calls[0][-1], "commit message should reference the app slug, not the build key"


def test_main_finds_nothing_when_a_value_only_exists_under_the_build_key(monkeypatch, tmp_path):
    """Locks in the fix itself: a value stored under the *build* key
    (reddit-adobo) rather than the app slug (reddit) - which is not what
    a real run ever produces, but is exactly what the pre-fix bug used to
    look for - must not be found or committed."""
    monkeypatch.setattr(catalog, "BUILDS", {"reddit-adobo": _fake_build("reddit-adobo", "reddit")})
    monkeypatch.setattr(sys, "argv", ["commit_signature.py", "reddit-adobo"])

    known_path = tmp_path / "known_signatures.json"
    pending_path = tmp_path / "pending_signatures.json"
    known_path.write_text("{}")
    pending_path.write_text(json.dumps({"reddit-adobo": "should-not-be-found-by-build-key"}))
    monkeypatch.setattr(commit_signature, "FILES", [known_path, pending_path])

    git_calls = []
    monkeypatch.setattr(commit_signature, "run", _fake_run(git_calls))

    commit_signature.main()

    assert git_calls == [], "nothing keyed by app_slug ('reddit') exists, so nothing should be touched at all"


def test_main_is_a_noop_when_the_app_slug_value_is_already_up_to_date(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "BUILDS", {"reddit-adobo": _fake_build("reddit-adobo", "reddit")})
    monkeypatch.setattr(sys, "argv", ["commit_signature.py", "reddit-adobo"])

    known_path = tmp_path / "known_signatures.json"
    pending_path = tmp_path / "pending_signatures.json"
    known_path.write_text("{}")
    pending_path.write_text(json.dumps({"reddit": "already-current"}))
    monkeypatch.setattr(commit_signature, "FILES", [known_path, pending_path])

    git_calls = []
    # origin/main already has this exact value (no reset_files override:
    # "git reset --hard" is a no-op here, so the re-read after it sees the
    # same value the local pass already found).
    monkeypatch.setattr(commit_signature, "run", _fake_run(git_calls))

    commit_signature.main()

    commit_calls = [c for c in git_calls if c[:2] == ["git", "commit"]]
    assert commit_calls == [], "value already matches origin/main, nothing to commit"


def test_main_exits_quietly_for_an_unknown_build_key(monkeypatch):
    monkeypatch.setattr(catalog, "BUILDS", {})
    monkeypatch.setattr(sys, "argv", ["commit_signature.py", "not-a-real-build"])

    git_calls = []
    monkeypatch.setattr(commit_signature, "run", _fake_run(git_calls))

    commit_signature.main()  # must not raise

    assert git_calls == []
