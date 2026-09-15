import re

from morphe_builder.version_policy import pick_version


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


def pick_latest_version(versions: list[dict]) -> str | None:
    """Backwards-compatible wrapper for the historical selection policy."""
    return pick_version(versions, "max_patches_then_version")


def to_apkmirror_version(version: str) -> str:
    return version.replace(".", "-")
