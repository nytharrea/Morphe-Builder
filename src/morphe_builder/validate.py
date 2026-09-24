"""validate_catalog() cross-checks catalog/apps.yaml + catalog/patch_sources.yaml
for internal consistency: every build's patch_sources actually exist,
exclude/enable are lists (a bare string would get patch_apk() to pass one
--disable/--enable flag per *character* instead of one per patch name),
and each build's apk_source has the fields its own type needs (org+slug
for apkmirror, owner+repo for github) - catalog.py's loader already
rejects an unrecognized type, but not a recognized type missing a field
it needs.

There's deliberately no PROCESS_ORDER<->matrix.app cross-check here anymore
(the old version of this function had one). That whole class of drift is
gone now, not just detected: .github/workflows/patch.yml's `patch` job
reads its matrix straight from this same catalog at run time (see the
`prepare` job's "Compute release plan" step), so there is only one list of
builds to ever go stale against the workflow - itself.
"""

from .catalog import BUILDS, PATCH_SOURCES
from .settings import settings


class ConfigError(Exception):
    pass


def validate_catalog() -> None:
    problems: list[str] = []

    for key, build in BUILDS.items():
        sources = build.get("patch_sources") or []

        if not sources:
            problems.append(f'Build "{key}" has no patch_sources.')

        for s in sources:
            if s not in PATCH_SOURCES:
                problems.append(
                    f'Build "{key}" references patch source "{s}", which is not in '
                    f"{settings.patch_sources_catalog_path}."
                )

        for field in ("exclude", "enable"):
            value = build.get(field)
            if value is not None and not isinstance(value, list):
                problems.append(
                    f'Build "{key}".{field} should be a list, got {type(value).__name__} '
                    f"(a bare string would get patch_apk() to pass one --disable/--enable flag per "
                    f"character instead of one per patch name)."
                )

        # catalog.py's own loader already rejects an apk_source.type that
        # isn't "apkmirror" or "github" - what it doesn't check is whether
        # the fields *that type* actually needs are present. Missing ones
        # would otherwise only surface as a raw KeyError deep inside
        # apkmirror.py/github_app.py, mid-run, for whichever build hits it
        # first, instead of every problem being reported here up front.
        source = build.get("apk_source") or {}
        required_fields = {"apkmirror": ("org", "slug"), "github": ("owner", "repo")}.get(source.get("type"), ())
        for field in required_fields:
            if not source.get(field):
                problems.append(f'Build "{key}".apk_source is type "{source.get("type")}" but has no "{field}".')

    if problems:
        header = f"Found {len(problems)} problem(s) in the catalog:"
        raise ConfigError("\n".join([header] + [f"  - {p}" for p in problems]))
