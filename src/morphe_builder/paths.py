"""Shared filesystem-location helpers.

The pipeline always runs from the repo root (every script's own "Run as
`python scripts/x.py` from the repo root" contract, plus CI's checkout
step, keep that invariant), so resolving a path from repo_root() at the
point you actually need it is equivalent to hardcoding the repo's
absolute path - except it keeps working if that root ever moves.

The one thing to avoid is caching a path built from repo_root() as a
module-level constant: that freezes in whatever the cwd happened to be
at import time instead of at the point of use, so if anything ever did
os.chdir() between import and use, a cached constant would silently
point at the wrong place while a fresh call here wouldn't. Nothing in
this codebase calls chdir today - this module exists to consolidate
what used to be two independent copies of repo_root() (apkmirror.py,
github_app.py) plus a module-level DIAGNOSTICS_DIR built from one of
them, into one place, resolved fresh on every call.
"""

from pathlib import Path


def repo_root() -> Path:
    return Path.cwd()


def diagnostics_dir() -> Path:
    return repo_root() / "diagnostics"


def downloads_dir() -> Path:
    return repo_root() / "downloads"
