from collections import Counter
from pathlib import Path

import yaml

from .config import APKMIRROR_APPS, APPS_CONFIG, PATCH_SOURCES, PROCESS_ORDER
from .sources import apkmirror, github_apk

_WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "patch.yml"


class ConfigError(Exception):
    pass


def _matrix_apps_from_workflow(path: Path) -> list[str]:
    """Read the `patch` job's `matrix.app` list straight out of patch.yml,
    so validate_config() can catch it drifting from PROCESS_ORDER instead
    of that only ever surfacing as an app silently never getting built in
    CI (or a matrix job that fails every run)."""
    with path.open(encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    matrix_app = workflow["jobs"]["patch"]["strategy"]["matrix"]["app"]
    if not isinstance(matrix_app, list):
        raise TypeError(f"jobs.patch.strategy.matrix.app should be a list, got {type(matrix_app).__name__}")
    return matrix_app


def validate_config() -> None:
    problems: list[str] = []

    for app_key in PROCESS_ORDER:
        if app_key not in APPS_CONFIG:
            problems.append(f'PROCESS_ORDER has "{app_key}" but APPS_CONFIG has no such key.')

    for app_key in APPS_CONFIG:
        if app_key not in PROCESS_ORDER:
            problems.append(f'APPS_CONFIG["{app_key}"] is missing from PROCESS_ORDER, so it will never run.')

    try:
        matrix_apps = _matrix_apps_from_workflow(_WORKFLOW_PATH)
    except Exception as e:
        problems.append(
            f"Could not read jobs.patch.strategy.matrix.app from {_WORKFLOW_PATH}: {e}. "
            f"Skipping the PROCESS_ORDER <-> matrix.app cross-check."
        )
    else:
        process_order_set = set(PROCESS_ORDER)
        matrix_set = set(matrix_apps)

        for app_key in sorted(process_order_set - matrix_set):
            problems.append(
                f'"{app_key}" is in PROCESS_ORDER but missing from patch.yml\'s matrix.app - '
                f"it will run in a local/manual full run but CI will never build it."
            )
        for app_key in sorted(matrix_set - process_order_set):
            problems.append(
                f'"{app_key}" is in patch.yml\'s matrix.app but not in PROCESS_ORDER/APPS_CONFIG - '
                f"that matrix job will fail every run."
            )

        app_counts = Counter(matrix_apps)
        duplicates = sorted(app for app, count in app_counts.items() if count > 1)
        if duplicates:
            problems.append(f"patch.yml's matrix.app lists these app(s) more than once: {duplicates}.")

    for app_key, cfg in APPS_CONFIG.items():
        source = cfg.get("patch_source")
        sources = source if isinstance(source, list) else [source]

        if not sources:
            problems.append(f'APPS_CONFIG["{app_key}"].patch_source is an empty list.')

        for s in sources:
            if s not in PATCH_SOURCES:
                problems.append(f'APPS_CONFIG["{app_key}"].patch_source = "{s}" is not a key in PATCH_SOURCES.')

        name = cfg.get("name")
        if not name:
            problems.append(f'APPS_CONFIG["{app_key}"] has no "name".')
            continue

        if name in APKMIRROR_APPS:
            if name not in apkmirror.APP_SITES:
                problems.append(
                    f'APPS_CONFIG["{app_key}"].name = "{name}" is listed in APKMIRROR_APPS '
                    f"but has no entry in apkmirror.APP_SITES."
                )
        else:
            if name not in github_apk.DIRECT_REPOS:
                problems.append(
                    f'APPS_CONFIG["{app_key}"].name = "{name}" is not in APKMIRROR_APPS, and is also '
                    f"missing from sources/github_apk.py's DIRECT_REPOS - main.py would have no "
                    f"way to download it."
                )

        for field in ("exclude", "enable"):
            value = cfg.get(field)
            if value is not None and not isinstance(value, list):
                problems.append(
                    f'APPS_CONFIG["{app_key}"].{field} should be a list, got {type(value).__name__} '
                    f"(a bare string would get patch_apk() to pass one --disable/--enable flag per "
                    f"character instead of one per patch name)."
                )

    if problems:
        header = f"Found {len(problems)} problem(s) in core/config.py:"
        raise ConfigError("\n".join([header] + [f"  - {p}" for p in problems]))
