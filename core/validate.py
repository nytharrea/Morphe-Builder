from .config import APKMIRROR_APPS, APPS_CONFIG, PATCH_SOURCES, PROCESS_ORDER
from .sources import apkmirror, github_apk


class ConfigError(Exception):
    pass


def validate_config() -> None:
    problems: list[str] = []

    if len(PROCESS_ORDER) != len(set(PROCESS_ORDER)):
        problems.append("PROCESS_ORDER contains duplicate app keys.")

    for app_key in PROCESS_ORDER:
        if app_key not in APPS_CONFIG:
            problems.append(f'PROCESS_ORDER has "{app_key}" but APPS_CONFIG has no such key.')

    for app_key in APPS_CONFIG:
        if app_key not in PROCESS_ORDER:
            problems.append(f'APPS_CONFIG["{app_key}"] is missing from PROCESS_ORDER, so it will never run.')

    for app_key, cfg in APPS_CONFIG.items():
        required = ("pkg", "name", "patch_source", "arch", "icon")
        for field in required:
            if not cfg.get(field):
                problems.append(f'APPS_CONFIG["{app_key}"] is missing required field "{field}".')
        if not isinstance(cfg.get("pkg"), str) or "." not in cfg.get("pkg", ""):
            problems.append(f'APPS_CONFIG["{app_key}"].pkg must be an Android package name.')
        if cfg.get("arch") not in {"arm64-v8a", "armeabi-v7a", "x86", "x86_64", "universal"}:
            problems.append(f'APPS_CONFIG["{app_key}"].arch is unsupported: {cfg.get("arch")!r}.')
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
