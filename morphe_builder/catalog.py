"""Loads and merges catalog/apps.yaml + catalog/patch_sources.yaml into the
flat, per-build structures the rest of the pipeline works with.

catalog/apps.yaml is deliberately *nested* (one entry per real app, each
with a list of one-or-more `builds`) so that an app patched from several
different patch sources - reddit/reddit-adobo, the five tiktok-* builds,
twitter/twitter-x - is written down as one app with several builds instead
of being duplicated across several near-identical top-level entries. That
nesting is friendlier to hand-edit, but the rest of the codebase (main
pipeline, release matching, signature commits) wants one flat "what do I do
for this job" record per build, the same shape core/config.py's old
APPS_CONFIG used to hand out directly. This module does that flattening
once, at import time, and everything downstream reads `BUILDS` exactly like
it used to read `APPS_CONFIG` - just keyed by build key, with an extra
`app_slug` field pointing back at the owning app (used for signature
pinning and for picking the apk_source).

`get_release_naming`/`patch_sources_for` are re-exported here for the same
reason they lived next to the data before: they're pure lookups over
`BUILDS`/`PATCH_SOURCES`, not fetching or patching logic.
"""

from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import yaml

from .settings import settings


class ApkMirrorSource(TypedDict):
    type: Literal["apkmirror"]
    org: str
    slug: str
    release_slug: NotRequired[str]


class GithubAppSource(TypedDict):
    type: Literal["github"]
    owner: str
    repo: str
    asset_hint: NotRequired[str]
    tag_template: NotRequired[str]


ApkSource = ApkMirrorSource | GithubAppSource


class PatchSource(TypedDict):
    owner: str
    repo: str
    label: str


class BuildConfig(TypedDict):
    key: str
    app_slug: str
    pkg: str
    display_name: str
    arch: str
    icon: str
    apk_source: ApkSource
    patch_sources: list[str]
    exclude: list[str]
    enable: list[str]
    force_version: str | None
    force_build: str | None


class CatalogError(Exception):
    """Raised for structural problems in catalog/*.yaml - a build key used
    twice, a required field missing, or a file that isn't even valid YAML.
    Deeper, cross-referential checks (a patch_source that doesn't exist, a
    non-list exclude/enable) are validate.validate_catalog()'s job, run
    explicitly by scripts/prepare_release.py; this module only guards
    against the catalog being malformed in a way that would silently lose
    or corrupt data while loading it.
    """


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CatalogError(f"Catalog file not found: {path}") from e
    except yaml.YAMLError as e:
        raise CatalogError(f"Could not parse {path}: {e}") from e

    if not isinstance(data, dict):
        raise CatalogError(f"{path} should contain a YAML mapping at the top level.")
    return data


def _load_patch_sources() -> dict[str, PatchSource]:
    raw = _load_yaml(settings.patch_sources_catalog_path)
    sources: dict[str, PatchSource] = {}

    for key, entry in raw.items():
        try:
            sources[key] = {
                "owner": entry["owner"],
                "repo": entry["repo"],
                "label": entry.get("label", key),
            }
        except KeyError as e:
            raise CatalogError(
                f'Patch source "{key}" in {settings.patch_sources_catalog_path} is missing required field {e}.'
            ) from e

    return sources


def _load_builds() -> dict[str, BuildConfig]:
    raw = _load_yaml(settings.apps_catalog_path)
    builds: dict[str, BuildConfig] = {}

    for app_slug, app in raw.items():
        try:
            apk_source: ApkSource = app["apk_source"]

            for build in app.get("builds") or []:
                key = build["key"]
                if key in builds:
                    raise CatalogError(
                        f'Build key "{key}" is used more than once in {settings.apps_catalog_path} '
                        f'(most recently under app "{app_slug}") - build keys must be unique across '
                        f"the whole catalog, since they double as CI matrix jobs and release filenames."
                    )
                if not build.get("patch_sources"):
                    raise CatalogError(f'Build "{key}" (app "{app_slug}") has no patch_sources.')

                builds[key] = {
                    "key": key,
                    "app_slug": app_slug,
                    "pkg": app["pkg"],
                    "display_name": build.get("display_name", app["display_name"]),
                    "arch": app["arch"],
                    "icon": app["icon"],
                    "apk_source": apk_source,
                    "patch_sources": list(build["patch_sources"]),
                    "exclude": list(build.get("exclude") or []),
                    "enable": list(build.get("enable") or []),
                    "force_version": build.get("force_version"),
                    "force_build": build.get("force_build"),
                }
        except KeyError as e:
            raise CatalogError(
                f'App "{app_slug}" in {settings.apps_catalog_path} is missing required field {e}.'
            ) from e

    return builds


PATCH_SOURCES: dict[str, PatchSource] = _load_patch_sources()
BUILDS: dict[str, BuildConfig] = _load_builds()


def patch_sources_for(build_key: str) -> list[str]:
    return BUILDS[build_key]["patch_sources"]


def get_release_naming(build_key: str) -> tuple[str, str | None]:
    """Pick the display name for a build's release filename and, if some
    other build shares that same display name (today: only the five
    tiktok-* builds), a disambiguating suffix - that build's primary
    (first) patch source's GitHub owner."""
    build = BUILDS[build_key]
    display_name = build["display_name"]

    siblings = [b for b in BUILDS.values() if b["display_name"] == display_name]
    if len(siblings) <= 1:
        return display_name, None

    primary_source = build["patch_sources"][0]
    return display_name, PATCH_SOURCES[primary_source]["owner"]
