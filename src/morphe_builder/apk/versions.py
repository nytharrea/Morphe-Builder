import re
import time

from .. import log, paths

# Matches a "Most common compatible versions" entry line from morphe-desktop's
# ListCompatibleVersions.kt, e.g.:
#   19.35.36 (5 patches)
#   19.35.35 [versionCodes: ARM64_V8A=331058270, ARMEABI_V7A=331058271] (3 patches)
#   19.35.34 (1 patch)
# The "[versionCodes: ...]" segment is only present when the patches
# bundle records per-ABI version codes for that version, and the count is
# singular ("1 patch") rather than plural exactly when it's 1.
_VERSION_LINE_RE = re.compile(r"^(.+?)(?:\s+\[[^\]]*\])?\s+\((\d+)\s+patch(?:es)?\)$")


def extract_cli_versions(output: str, app_slug: str | None = None) -> list[dict]:
    """Parses the CLI's "Most common compatible versions" section: the
    patches' own recorded compatibility data, which matters regardless of
    where the APK itself is downloaded from - a version the patches
    weren't written against can fail to apply, or apply but misbehave,
    even if it's otherwise the newest release. Returns an empty list in
    two routine (not exceptional) cases the caller is expected to fall
    through from to its own actual-latest-version lookup (APKMirror's
    real listing, or a GitHub repo's real latest release), which is more
    trustworthy than guessing a version out of unrelated CLI banner text:
    - The CLI doesn't print the section at all - no patch anywhere
      mentions this package by name.
    - The section prints the literal line "Any" - patches mention this
      package, but only with universal (any-version) compatibility, so
      there's no specific version to recommend.
    app_slug is optional and only used to name a diagnostics dump if the
    section header is found but unparseable (distinct from the "Any"
    case above - this is for a genuine, unrecognized line format) - pass
    it when available so that dump is easy to trace back to the app it
    came from.
    """
    results = []
    lines = output.split("\n")
    in_section = False
    found_header = False
    saw_any = False

    for line in lines:
        trimmed = line.strip()

        if trimmed.startswith("Most common compatible versions"):
            in_section = True
            found_header = True
            continue

        if in_section and not trimmed:
            break

        if in_section:
            if trimmed == "Any":
                # Only universal-compatibility patches for this package -
                # the CLI's own way of saying "no specific version to
                # recommend", not a parsing failure.
                saw_any = True
                continue
            match = _VERSION_LINE_RE.match(trimmed)
            if match:
                results.append({"version": match.group(1), "patches": int(match.group(2))})

    if found_header and not results and not saw_any:
        # The header was there but nothing under it matched the expected
        # line format (and it wasn't the legitimate "Any" case either) -
        # a real mismatch between this parser and the CLI's actual
        # output, worth flagging loudly rather than silently falling
        # through as if there were simply no data. Save the raw text
        # too: without it, a fix here is a guess, not a diagnosis.
        log.warn(
            "Found the 'Most common compatible versions' section but couldn't parse any lines under it - the "
            "CLI's output format may have changed. Falling through to the actual latest version instead of a "
            "patch-recommended one."
        )
        _save_diagnostic_output(output, app_slug)

    return results


def _save_diagnostic_output(output: str, app_slug: str | None) -> None:
    try:
        diagnostics_dir = paths.diagnostics_dir()
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        label = f"list-versions-{app_slug or 'unknown'}"
        path = diagnostics_dir / f"{label}-{int(time.time())}.txt"
        path.write_text(output or "", encoding="utf-8", errors="replace")
        log.info(f"Diagnostic CLI output saved: {path}")
    except OSError as e:
        log.warn(f"Could not save diagnostic CLI output: {e}")


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
