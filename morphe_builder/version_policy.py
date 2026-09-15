from __future__ import annotations


def version_core_tuple(version: str) -> tuple[int, ...]:
    core = version.split("-", 1)[0]
    parts = core.split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return (0,)


def pick_version(versions: list[dict], policy: str = "max_patches_then_version") -> str | None:
    """Pick a version according to the configured policy.

    max_patches_then_version preserves the historical behaviour: highest patch
    count first, then highest version core. latest_compatible reverses that:
    highest version first, then highest patch count.
    """
    if not versions:
        return None

    if policy == "latest_compatible":
        return max(
            versions,
            key=lambda item: (version_core_tuple(str(item.get("version", ""))), int(item.get("patches", 0))),
        )["version"]

    return max(
        versions,
        key=lambda item: (int(item.get("patches", 0)), version_core_tuple(str(item.get("version", "")))),
    )["version"]
