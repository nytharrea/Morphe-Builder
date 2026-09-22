"""validate_catalog() cross-checks catalog/apps.yaml + catalog/patch_sources.yaml
for internal consistency: every build's patch_sources actually exist, and
exclude/enable are lists (a bare string would get patch_apk() to pass one
--disable/--enable flag per *character* instead of one per patch name).

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

    if problems:
        header = f"Found {len(problems)} problem(s) in the catalog:"
        raise ConfigError("\n".join([header] + [f"  - {p}" for p in problems]))
