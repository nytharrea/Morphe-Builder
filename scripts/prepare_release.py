"""Entry point for the `prepare` job.

1. Validate catalog
2. Download shared assets (jar + .mpp) — needed for version resolution
3. Run the build planner (compare current versions/config vs previous
   release's build-manifest.json)
4. Emit GitHub Actions outputs: tag, name, matrix (only BUILD keys),
   and write plan.json for the finalize job.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from morphe_builder import log
from morphe_builder.build.planner import create_plan
from morphe_builder.settings import settings
from morphe_builder.validate import validate_catalog


async def _async_main() -> None:
    try:
        validate_catalog()
    except Exception as e:
        log.error(f"Catalog validation failed:\n{e}")
        sys.exit(1)

    date = datetime.now(UTC)
    tag = f"build-{date.strftime('%Y-%m-%dT%H-%M-%S')}"
    name = f"Patched APKs - {date.day} {date.strftime('%B %Y')}"

    force_rebuild = os.environ.get("FORCE_REBUILD", "").lower() in ("1", "true", "yes")
    target_app = settings.target_app or "all"

    log.info(f"Release tag for this run: {tag}")
    log.info(f"Release name for this run: {name}")
    if force_rebuild:
        log.warn("FORCE_REBUILD is set — every selected build will run")

    plan = await create_plan(
        release_tag=tag,
        release_name=name,
        force_rebuild=force_rebuild,
        target_app=target_app,
    )

    # Empty matrix is valid (everything skipped) — GitHub Actions still runs
    # finalize so the release snapshot is re-published with carried-over APKs.
    matrix_keys = plan.to_build
    matrix = json.dumps(matrix_keys)

    plan_path = Path.cwd() / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info(f"Wrote plan.json ({len(plan.decisions)} decision(s))")

    log.info(f"Build matrix ({len(matrix_keys)} build(s)): {matrix}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"tag={tag}\n")
            f.write(f"name={name}\n")
            f.write(f"matrix={matrix}\n")
            f.write(f"build_count={len(matrix_keys)}\n")
            f.write(f"skip_count={len(plan.to_skip)}\n")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
