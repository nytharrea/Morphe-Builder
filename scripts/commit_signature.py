"""Entry point for the `patch` job's "Commit signature record if new"
step. Run as `python scripts/commit_signature.py <build_key>` from the repo
root (falls back to TARGET_APP if no argument is given).

Takes a *build* key (e.g. "reddit-adobo", matrix.app's value) but signature
records in signatures/*.json are keyed by *app slug* ("reddit" - the same
app reddit-adobo shares with the plain "reddit" build, see
catalog/apps.yaml). Resolving build_key -> app_slug via the catalog before
touching those files is the fix for a real bug this restructure otherwise
would have carried forward: previously this script was invoked with the
build key directly and looked THAT up in signatures/*.json, which are
keyed by app slug - so for every build whose key differs from its app's
slug (every multi-source build: reddit-adobo, twitter-x, and all four
non-primary tiktok-* builds) this step always found nothing and silently
committed nothing, even on a run where verify.py had just pinned a new
signature for that app.
"""

import json
import subprocess
import sys
from pathlib import Path

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt

from morphe_builder import catalog, log
from morphe_builder import retry as retry_conf
from morphe_builder.settings import settings

FILES = [settings.known_signatures_path, settings.pending_signatures_path]


class _PushConflict(Exception):
    pass


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def main():
    build_key = sys.argv[1] if len(sys.argv) > 1 else settings.target_app
    if not build_key:
        log.info("Build key not provided, exiting.")
        return

    build = catalog.BUILDS.get(build_key)
    if build is None:
        log.warn(f'"{build_key}" is not a known build key (check catalog/apps.yaml), exiting.')
        return
    app_slug = build["app_slug"]

    local_values = {}
    for fname in FILES:
        data = load(fname)
        if app_slug in data:
            local_values[fname] = data[app_slug]

    if not local_values:
        log.info(f"No new signature record to commit for {app_slug} (build: {build_key}).")
        return

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])

    def _attempt() -> None:
        """One fetch-reset-reapply-commit-push cycle. Returns normally once
        there's genuinely nothing left to do (already up to date / nothing
        real to commit) or the push succeeds; raises _PushConflict to
        trigger a retry otherwise."""
        run(["git", "fetch", "origin", "main"])
        run(["git", "reset", "--hard", "origin/main"])

        changed = False
        for fname, value in local_values.items():
            data = load(fname)
            if data.get(app_slug) != value:
                data[app_slug] = value
                fname.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
                changed = True

        if not changed:
            log.info(f"{app_slug} is already up to date on main, skipping commit.")
            return

        run(["git", "add", *FILES])
        commit = run(["git", "commit", "-m", f"chore: update signature record for {app_slug} [skip ci]"])
        if commit.returncode != 0:
            log.info("No real change to commit.")
            return

        push = run(["git", "push", "origin", "HEAD:main"])
        if push.returncode == 0:
            log.success(f"Committed signature record for {app_slug}.")
            return

        raise _PushConflict(push.stderr.strip() or "git push failed")

    try:
        for attempt in Retrying(
            stop=stop_after_attempt(6),
            wait=retry_conf.incrementing(start=2.0, increment=2.0, jitter=4.0),
            retry=retry_if_exception_type(_PushConflict),
            before_sleep=retry_conf.before_sleep(f"Push conflict for {app_slug}"),
            reraise=True,
        ):
            with attempt:
                _attempt()
    except _PushConflict:
        log.error(f"Could not commit signature record for {app_slug} (all retries exhausted).")
        sys.exit(1)


if __name__ == "__main__":
    main()
