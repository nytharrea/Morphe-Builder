"""Entry point for the `prepare` job: validates catalog/*.yaml, computes
this run's release tag/name, and emits the build matrix that the `patch`
job's `strategy.matrix.app` reads via fromJson() - the catalog is now the
only place a build list is ever written down, so there's nothing left for
it to drift out of sync with. Run as `python scripts/prepare_release.py`
from the repo root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import os
from datetime import UTC, datetime

from morphe_builder import catalog, log
from morphe_builder.validate import validate_catalog


def main():
    try:
        validate_catalog()
    except Exception as e:
        log.error(f"Catalog validation failed:\n{e}")
        sys.exit(1)

    date = datetime.now(UTC)
    tag = f"build-{date.strftime('%Y-%m-%dT%H-%M-%S')}"
    name = f"Patched APKs - {date.day} {date.strftime('%B %Y')}"
    matrix = json.dumps(list(catalog.BUILDS))

    log.info(f"Release tag for this run: {tag}")
    log.info(f"Release name for this run: {name}")
    log.info(f"Build matrix ({len(catalog.BUILDS)} build(s)): {matrix}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"tag={tag}\n")
            f.write(f"name={name}\n")
            f.write(f"matrix={matrix}\n")


if __name__ == "__main__":
    main()
