import re


def extract_youtube_versions(output: str) -> list[dict]:
    results = []
    lines = output.split("\n")
    in_section = False

    for line in lines:
        trimmed = line.strip()

        if trimmed.startswith("Most common compatible versions"):
            in_section = True
            continue

        if in_section and not trimmed:
            break

        if in_section:
            match = re.match(
                r"^(\d+(?:\.\d+){1,4}(?:-[a-zA-Z]+\.\d+)?)\s+\((\d+)\s+patches\)",
                trimmed,
            )
            if match:
                results.append({"version": match.group(1), "patches": int(match.group(2))})

    if not results:
        fallback = re.findall(r"\d+(?:\.\d+){1,4}(?:-[a-zA-Z]+\.\d+)?", output)
        return [{"version": v, "patches": 0} for v in fallback]

    return results


def _version_core(version: str) -> str:
    return version.split("-")[0]


def _version_sort_key(item: dict) -> tuple:
    parts = _version_core(item["version"]).split(".")
    try:
        core = tuple(int(p) for p in parts)
    except ValueError:
        core = (0,)
    return (item.get("patches", 0), core)


def pick_latest_version(versions: list[dict]) -> str | None:
    if not versions:
        return None
    best = max(versions, key=_version_sort_key)
    return best["version"]


def rank_versions(versions: list[dict], *, limit: int = 12) -> list[str]:
    """Return patcher-compatible versions ordered best-first (unique).

    Prefer higher patch-count, then higher version number. Used when the top
    pick is missing from APKMirror so we can fall through the list for any app.
    """
    if not versions:
        return []
    ordered = sorted(versions, key=_version_sort_key, reverse=True)
    seen: set[str] = set()
    ranked: list[str] = []
    for item in ordered:
        v = item["version"]
        if v in seen:
            continue
        seen.add(v)
        ranked.append(v)
        if len(ranked) >= limit:
            break
    return ranked


def to_apkmirror_version(version: str) -> str:
    return version.replace(".", "-")
