"""Entry point for the `prepare` job's "Download shared patch assets" step.

Every matrix `patch` job used to call download_latest_release_asset() for
the morphe-desktop CLI jar and its own patch sources' .mpp files
independently - the same handful of files, downloaded once per job, up to
`max-parallel` times in parallel every run. That's wasted bandwidth and,
worse, means a single transient failure fetching a shared asset (an
upstream HTTP 500, say) takes down whichever one matrix job hit it instead
of being a single shared point of failure the whole run can retry once.

This script downloads those same shared files exactly once, into the repo
root, using the exact same download_latest_release_asset() (so it's the
exact same asset-matching/retry logic patch.py itself uses - nothing
duplicated). The workflow then uploads whatever landed here as one
artifact and re-downloads it into every matrix job's own repo root before
scripts/patch.py runs. download_latest_release_asset() already skips
re-downloading a file that's already present on disk under its release
asset name, so patch.py needs no changes at all: its own calls just
cache-hit on the pre-placed files instead of hitting the network - and if
the artifact step is ever skipped or a job's checkout doesn't have these
files for some reason, patch.py's calls still work standalone, exactly as
before this script existed.

Run as `python scripts/download_shared_assets.py` from the repo root, in
the `prepare` job, after scripts/prepare_release.py.
"""

import asyncio

from morphe_builder import catalog, log
from morphe_builder.fetchers.release_assets import download_latest_release_asset


async def main() -> None:
    await download_latest_release_asset(
        owner="MorpheApp",
        repo="morphe-desktop",
        prerelease=True,
        match=lambda n: "desktop" in n and n.endswith(".jar"),
    )

    for key, source in catalog.PATCH_SOURCES.items():
        log.step(f"Fetching shared patch asset for '{key}' ({source['owner']}/{source['repo']})...")
        await download_latest_release_asset(
            owner=source["owner"],
            repo=source["repo"],
            prerelease=True,
            match=lambda n: n.endswith(".mpp"),
        )


if __name__ == "__main__":
    asyncio.run(main())
