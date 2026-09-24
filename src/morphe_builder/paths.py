"""Shared filesystem-location helpers.

repo_root() is anchored to this file's own on-disk location
(paths.py lives at <repo root>/src/morphe_builder/paths.py, so its
great-grandparent directory is the repo root) rather than to the
process's current working directory. That keeps every path built from
it correct even if something ever runs from a different cwd or calls
os.chdir() - instead of depending on the "always run from repo root"
convention every script's own "Run as `python scripts/x.py` from the
repo root" docstring otherwise has to declare and CI's checkout step
has to uphold.

settings.py's own *_path fields resolve through repo_root() for the
same reason, even though each is only computed once, at Settings()
construction time (a pydantic default_factory, evaluated when the
module-level `settings` singleton is built): since repo_root() no
longer reads any mutable process state, caching its result the one
time it's called is no longer a hazard the way caching a
Path.cwd()-derived value would be. This module exists to consolidate
what used to be two independent copies of repo_root() (apkmirror.py,
github_app.py) plus a module-level DIAGNOSTICS_DIR built from one of
them, into this one place.
"""

from pathlib import Path


def repo_root() -> Path:
    # paths.py -> morphe_builder -> src -> repo root
    return Path(__file__).resolve().parents[2]


def diagnostics_dir() -> Path:
    return repo_root() / "diagnostics"


def downloads_dir() -> Path:
    return repo_root() / "downloads"
