import re

from .. import log


def extract_cli_versions(output: str) -> list[dict]:
    """Parses the CLI's "Most common compatible versions" section: the
    patches' own recorded compatibility data, which matters regardless of
    where the APK itself is downloaded from - a version the patches
    weren't written against can fail to apply, or apply but misbehave,
    even if it's otherwise the newest release. Returns an empty list when
    the CLI doesn't print this section at all (routine: many apps,
    especially ones with a single broadly-compatible patch, have no
    per-version data to report) - the caller is expected to fall through
    to its own actual-latest-version lookup in that case (APKMirror's
    real listing, or a GitHub repo's real latest release), which is more
    trustworthy than guessing a version out of unrelated CLI banner text.
    """
    results = []
    lines = output.split("\n")
    in_section = False
    found_header = False

    for line in lines:
        trimmed = line.strip()

        if trimmed.startswith("Most common compatible versions"):
            in_section = True
            found_header = True
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

    if found_header and not results:
        # The header was there but nothing under it matched the expected
        # "X.Y.Z (N patches)" line format - a real mismatch between this
        # parser and the CLI's actual output, worth flagging loudly
        # rather than silently falling through as if there were simply
        # no data.
        log.warn(
            "Found the 'Most common compatible versions' section but couldn't parse any lines under it - the "
            "CLI's output format may have changed. Falling through to the actual latest version instead of a "
            "patch-recommended one."
        )

    return results


def _version_core(version: str) -> str:
    return version.split("-")[0]


def pick_latest_version(versions: list[dict]) -> str | None:
    if not versions:
        return None

    def sort_key(item: dict):
        parts = _version_core(item["version"]).split(".")
        try:
            core = tuple(int(p) for p in parts)
        except ValueError:
            core = (0,)
        return (item["patches"], core)

    best = max(versions, key=sort_key)
    return best["version"]


def to_apkmirror_version(version: str) -> str:
    return version.replace(".", "-")
