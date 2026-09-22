# Morphe-Builder

A GitHub Actions pipeline that downloads Android APKs, patches them (ReVanced-style, via a `morphe-desktop` CLI jar), re-signs them with your own release key, and publishes all of them together as a single GitHub Release. Everything runs on GitHub-hosted runners — no local machine required.

## Table of Contents

- [How It Works](#how-it-works)
- [Setup](#setup)
- [Running It](#running-it)
- [Verifying a Downloaded APK](#verifying-a-downloaded-apk)
- [Supported Apps](#supported-apps)
- [Adding a New App](#adding-a-new-app)
- [Project Structure](#project-structure)
- [CI (Lint & Test)](#ci-lint--test)
- [Configuration Reference](#configuration-reference)
- [License](#license)

## How It Works

One workflow, `.github/workflows/patch.yml`, runs as four jobs:

1. **`prepare`** — installs dependencies from the lockfile, validates `catalog/apps.yaml` + `catalog/patch_sources.yaml` for internal consistency, and computes this run's release tag/name and its build matrix (`scripts/prepare_release.py`) — the matrix is read straight out of the catalog, so it can never drift out of sync with it the way a separately hand-maintained list could.
2. **`patch`** — a matrix job, one runner per build (see [Supported Apps](#supported-apps)), running in parallel. Each runner downloads that build's original APK (from APKMirror, via a [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) sidecar container that clears its Cloudflare challenge, or directly from a GitHub release), verifies its signing certificate against a pinned fingerprint, patches it with the matching patch bundle(s), re-signs it with your keystore, and uploads it as a build artifact.
3. **`finalize`** — downloads every artifact the matrix produced, matches each file back to its build, builds one release description (with per-app version numbers and collapsible patch-source changelogs), creates a single GitHub Release with every APK attached, uploads MicroG/PotHelper companions if YouTube or YT Music was patched, deletes older releases, and sends a Discord/Telegram/Apprise notification.
4. **`cleanup`** — deletes old workflow runs to keep the Actions tab tidy.

The workflow only runs when you trigger it manually (`workflow_dispatch`) — there's no schedule configured. See [Running It](#running-it) to add one if you want automatic runs.

A **separate** workflow, `.github/workflows/lint.yml`, runs `ruff`, `mypy`, and `pytest` on every push to `main` and on every pull request.

## Setup

### 1. Fork or use this repo

Push it to your own GitHub account/org — the workflow needs write access to create releases and commit signature records. Licensed GPL-3.0 (see [License](#license)) — forks and modified versions stay under the same terms.

### 2. Repository permissions

**Settings → Actions → General → Workflow permissions** → select **"Read and write permissions"**. Without this, the `GITHUB_TOKEN` the workflow uses can't push commits, create releases, or upload artifacts.

### 3. Required secrets

**Settings → Secrets and variables → Actions → New repository secret.**

| Secret | Required | Purpose |
|---|---|---|
| `KEYSTORE_BASE64` | Yes, for your own signing key | Your Android signing keystore (`.jks`/`.keystore`), base64-encoded. Generate one with `keytool -genkeypair -v -keystore release.keystore -alias <your-alias> -keyalg RSA -keysize 2048 -validity 10000`, then `base64 -i release.keystore \| tr -d '\n'` and paste the output as the secret value. |
| `KEYSTORE_PASSWORD` | Yes, alongside the above | The keystore's password. |
| `KEY_ALIAS` | Yes, alongside the above | The `-alias` you used when generating the keystore. |
| `KEY_PASSWORD` | Yes, alongside the above | The key entry's password (often the same as `KEYSTORE_PASSWORD`). |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook URL for a run-summary notification. |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | No | Telegram bot token + chat ID, same purpose. |
| `APPRISE_URLS` | No | Any other [Apprise](https://github.com/caronc/apprise#supported-notifications)-supported target(s) (Slack, ntfy, Matrix, email, …), space/comma/newline-separated. |

`GITHUB_TOKEN` itself is provided automatically by GitHub Actions — you don't create it.

**If you skip the four `KEYSTORE_*` secrets**, `morphe_builder/apk/patcher.py` falls back to signing with the patcher tool's own shared default ("Morphe testkey") instead of failing — useful to try the pipeline quickly, but every fork that does this produces APKs signed with the *same* shared key, which isn't meaningfully "yours." Set your own keystore before you rely on this for real.

### 4. First run: pending signatures

The first time a given app runs, `signatures/known_signatures.json` won't have an entry for it yet, so `morphe_builder/apk/verify.py` deliberately fails that app and records the certificate fingerprint it saw in `signatures/pending_signatures.json` instead (auto-committed by `scripts/commit_signature.py`). This is expected. Verify that fingerprint against an official source for that app (its Play Store listing, the developer's own site, etc.), then move it into `signatures/known_signatures.json` under the app's slug (its top-level key in `catalog/apps.yaml` — see the existing entries for the format) and re-run. This is the pipeline protecting itself from ever patching an APK from an unexpected/compromised source without you noticing.

## Running It

**Actions tab → "Patch" workflow → Run workflow.** It processes every build listed in `catalog/apps.yaml`.

To patch a single build instead of all of them, either run it locally (`TARGET_APP=youtube python scripts/patch.py`, with the same env vars the workflow sets — see [Configuration Reference](#configuration-reference)) or temporarily comment out the builds you don't want in `catalog/apps.yaml` (the `patch` job's matrix is generated from that file at the start of every run, so there's no separate list in the workflow itself to edit).

To run on a schedule instead of only by hand, add a `schedule:` trigger under `on:` in `patch.yml`, e.g.:

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * *"
```

## Verifying a Downloaded APK

Every APK in the Releases tab is a modified build — not the original developer's file — so its signature is necessarily different from the app you'd get off the Play Store. Two ways to confirm a given file genuinely came from *this* pipeline and hasn't been swapped or tampered with:

### Method 1 — GitHub Artifact Attestation (recommended)

The `patch` job's last step (`actions/attest`) generates a signed, Sigstore-backed attestation for every APK it produces, proving it was built by this exact workflow run in this exact repo. Verify it with the [GitHub CLI](https://cli.github.com/):

```bash
gh attestation verify YouTube-<version>.apk --owner <your-github-username-or-org>
```

A successful verification confirms the file was built by a GitHub Actions run in your repo — no manual fingerprint bookkeeping needed, and it can't be spoofed by re-uploading a different file under the same name.

### Method 2 — Signing certificate fingerprint

Every APK is signed with the same key (yours, if you set up `KEYSTORE_BASE64` — see [Setup](#setup)), so every release's APKs should all share one consistent SHA-256 certificate fingerprint. Check it with `apksigner` (bundled with the Android SDK build-tools) or `keytool`:

```bash
apksigner verify --print-certs YouTube-<version>.apk
# or
keytool -printcert -jarfile YouTube-<version>.apk
```

Look for the `SHA-256` line under the certificate. Compare it against the value you record after your first successful run — it should stay identical release after release, for every app, as long as you keep using the same keystore secrets. A file with a different fingerprint did not come from your pipeline.

> Fill in your own fingerprint once you have it, so downloaders don't have to dig through workflow logs:
>
> **Expected signing certificate SHA-256:** `<paste yours here after your first run>`

Note this is a *different* fingerprint from the ones in `signatures/known_signatures.json` — those pin each app's **original**, pre-patch developer certificate (checked internally, before patching starts) so a compromised mirror can't slip in a malicious source file. The fingerprint above is your **own** re-signing key, checked on the **final**, patched file.

One practical consequence of re-signing: Android refuses to install an update over an app signed with a different key. If you ever change keystores, users have to uninstall the old APK first.

## Supported Apps

Each row is one **build** — a `key` under some app's `builds:` list in `catalog/apps.yaml`. That key is what `TARGET_APP` takes and what `signatures/known_signatures.json`/`pending_signatures.json` would use *if* it were per-build — it isn't: those files are keyed by the owning **app** slug instead (the `Package`/`Source` columns below are the same for every build of the same app), because the original, unpatched APK a build starts from is the same file regardless of which patch source ends up applied to it. Several rows patch the *same* underlying app from a different patch source (e.g. `tiktok` vs `tiktok-hxreborn`) — these are intentionally separate, independently downloaded and patched builds, not duplicates; that's exactly the pattern `catalog/apps.yaml`'s one-app-many-builds shape exists to represent.

| Key | Package | Source | Patch source(s) |
|---|---|---|---|
| `youtube` | `com.google.android.youtube` | APKMirror | 🟢 Morphe |
| `youtube-music` | `com.google.android.apps.youtube.music` | APKMirror | 🟢 Morphe |
| `reddit` | `com.reddit.frontpage` | APKMirror | 🟢 Morphe |
| `reddit-adobo` | `com.reddit.frontpage` | APKMirror | 🥘 Adobo |
| `twitter` | `com.twitter.android` | APKMirror | ✖️ Piko |
| `twitter-x` | `com.twitter.android` | APKMirror | 🆕 Piko NewX, 🟢 Morphe |
| `instagram` | `com.instagram.android` | APKMirror | ✖️ Piko |
| `gboard` | `com.google.android.inputmethod.latin` | APKMirror | ⌨️ JasonWu Gboard |
| `speedtest` | `org.zwanoo.android.speedtest` | APKMirror | ⚡ Rushiranpise, 🟢 Morphe |
| `brave` | `com.brave.browser` | APKMirror | 🦁 dh6k |
| `proton-vpn` | `ch.protonvpn.android` | APKMirror | 🍃 hoo-dles |
| `tiktok` | `com.zhiliaoapp.musically` | APKMirror | 🎵 TikTok Patches, 🟢 Morphe |
| `tiktok-hxreborn` | `com.zhiliaoapp.musically` | APKMirror | 🔥 hxreborn TikTok, 🟢 Morphe |
| `tiktok-bluedragon` | `com.zhiliaoapp.musically` | APKMirror | 🔷 BlueIT Service, 🟢 Morphe |
| `tiktok-hushfeed` | `com.zhiliaoapp.musically` | APKMirror | 🤫 Hushfeed, 🟢 Morphe |
| `tiktok-kveld` | `com.zhiliaoapp.musically` | APKMirror | 🌙 Kveld, 🟢 Morphe |
| `warp` | `com.cloudflare.onedotonedotonedotone` | APKMirror | ⚡ Rushiranpise |
| `inshot` | `com.camerasideas.instashot` | APKMirror | 🎬 Hooman's Patches |
| `google-photos` | `com.google.android.apps.photos` | APKMirror | ⚡ Rushiranpise |
| `inure-github` | `app.simple.inure` | GitHub | ⚡ Rushiranpise |
| `inure-play` | `app.simple.inure.play` | GitHub | ⚡ Rushiranpise |
| `proton-pass` | `proton.android.pass` | APKMirror | ⚡ Rushiranpise |
| `notesnook` | `com.streetwriters.notesnook` | APKMirror | 🔥 hxreborn |
| `fairemail` | `eu.faircode.email` | APKMirror | 💎 Heval |

"APKMirror" means the app's `apk_source.type` in `catalog/apps.yaml` is `apkmirror` — scraped from apkmirror.com through a [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) sidecar container that clears its Cloudflare challenge; "GitHub" means `apk_source.type` is `github` — downloaded directly from a GitHub release (`morphe_builder/fetchers/github_app.py`), which is faster and doesn't need FlareSolverr at all.

## Adding a New App

1. Add an entry to `catalog/apps.yaml`: the app's `pkg`, `display_name`, `arch`, `icon`, an `apk_source` (an `apkmirror` entry needs `org`/`slug`, plus `release_slug` only if the release page's own URL uses a different word than the folder `slug` does; a `github` entry needs `owner`/`repo`, plus `asset_hint`/`tag_template` if needed), and a `builds:` list with at least one build (a `key`, its `patch_sources`, and any `exclude`/`enable`/`force_version`/`force_build` overrides — see the existing entries, and the comment at the top of the file, for the exact shape).
2. If it uses a patch source not already in `catalog/patch_sources.yaml`, add it there too (`owner`, `repo`, `label`).
3. That's it for the CI side — the `patch` job's matrix is generated from `catalog/apps.yaml` at the start of every run, so there's no separate list anywhere else to keep in sync.
4. `python -c "from morphe_builder.validate import validate_catalog; validate_catalog()"` catches most catalog mistakes (a `patch_sources` entry that doesn't exist in `catalog/patch_sources.yaml`, or `exclude`/`enable` written as a bare string instead of a list) before you push.
5. Run it once — it will fail on purpose with a pending-signature message. Follow [First run: pending signatures](#4-first-run-pending-signatures) to pin its certificate, then run again.

## Project Structure

### `catalog/` — the app/patch-source data

| File | Purpose |
|---|---|
| `apps.yaml` | One entry per app (package, display name, arch, icon, and its `apk_source`), each holding a `builds:` list — almost always one build, but more than one for an app patched from several different sources (reddit/reddit-adobo, the five tiktok-\* builds, twitter/twitter-x) or with several sources combined into one output (speedtest). This is the pipeline's single source of truth for which builds exist, replacing what used to be several separate, hand-kept-in-sync Python dicts. |
| `patch_sources.yaml` | One entry per patch-bundle GitHub repo (`owner`, `repo`, emoji `label`), referenced by key from `apps.yaml`'s `builds[].patch_sources`. |

### `morphe_builder/` — shared library

| File | Purpose |
|---|---|
| `catalog.py` | Loads and merges the two files above into `BUILDS` (one flat, per-build record — the equivalent of what `APPS_CONFIG` used to hand out directly, now with an added `app_slug` field pointing back at the owning app) and `PATCH_SOURCES`, plus `patch_sources_for()`/`get_release_naming()` (picks a human display name and, if two builds of the same app share one, like the tiktok-\* builds all sharing "TikTok", disambiguates the release filename with that build's primary patch source's owner). |
| `settings.py` | One `pydantic-settings` `Settings` class declaring every environment variable the pipeline reads, case-insensitively (see [Configuration Reference](#configuration-reference)). |
| `log.py` | Leveled, colored console logging (`log.step`, `log.info`, `log.warn`, `log.notice`, `log.success`, `log.error`, …), a `NOTICE` level for expected/self-recovering events (retries, cooldowns) that stay out of GitHub's Warning annotations, GitHub Actions annotation output for real warnings/errors, and `patch_line()`, which classifies and re-colors the patcher CLI's own raw output line by line. |
| `retry.py` | Shared `tenacity` wait-strategy and `before_sleep` helpers used by every retry loop in the codebase, so backoff behavior and logging are consistent everywhere instead of hand-rolled per call site. |
| `http.py` | One shared `curl_cffi` session factory (`new_session`), configured to impersonate a current Firefox TLS/HTTP fingerprint, and `github_headers()` (adds `Authorization` only when a token is actually configured, instead of ever sending an empty `Bearer` value). |
| `release.py` | Thin GitHub Releases REST API wrapper: create a release, list/delete releases and tags, upload an asset (replacing one of the same name if present), and the MicroG/PotHelper companion-upload helpers. |
| `notify.py` | Sends the end-of-run summary through `apprise` to whichever of Discord/Telegram/Apprise-URL targets are configured; also builds the summary/all-failed message text. |
| `validate.py` | `validate_catalog()` — cross-checks the loaded catalog for internal consistency (every build has at least one `patch_sources` entry and every one of them exists in `catalog/patch_sources.yaml`, `exclude`/`enable` are actually lists), and raises one exception listing everything wrong at once. |

### `morphe_builder/apk/` — APK-level operations

| File | Purpose |
|---|---|
| `patcher.py` | Builds and runs the `java -jar morphe-desktop.jar patch …` command (patches, arch stripping, `--disable`/`--enable` flags, and keystore signing args if configured, else a warning and the tool's default test key), streams its output line by line through `log.patch_line`, and parses the resulting patched-APK path out of that output. |
| `verify.py` | Pre-patch signature pinning. Extracts the signing certificate(s) from a downloaded APK (unwrapping `.apkm`/`.xapk` bundles to their `base.apk` first if needed) via `androguard`, hashes them with `cryptography`, and compares against `signatures/known_signatures.json`. No pinned entry → records the fingerprint to `signatures/pending_signatures.json` and fails loudly. Mismatch → fails loudly ("this may indicate the APK came from an unexpected/untrusted source"). Can be bypassed with `SKIP_SIGNATURE_VERIFY=1` (not recommended). |
| `versions.py` | Parses the patcher CLI's `list-versions` output into `{version, patches}` entries, picks the best one (`pick_latest_version` — most compatible patches, then highest version number), and converts a version string into the dash-separated form APKMirror uses in its URLs. |

### `morphe_builder/fetchers/` — where apps and patches come from

| File | Purpose |
|---|---|
| `apkmirror.py` | Downloads apps from apkmirror.com. Resolves an app + version to the right APKMirror URL from the `org`/`slug`/`release_slug` its `catalog/apps.yaml` entry supplies, fetches each page through `flaresolverr.py`, parses the returned HTML (`lxml`) to pick the right variant row (architecture/DPI priority, with a bundle-only special case for apps like Instagram that APKMirror only ships as a split `.apkm`), handles Cloudflare challenge pages and rate-limit cooldowns, follows the download-button → confirm-page → final-file hop by reading each page's link `href` directly rather than clicking anything, and streams the file to disk. |
| `flaresolverr.py` | Talks to a local [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) instance (the `patch` job runs it as a `services:` sidecar container, `localhost:8191`) over its HTTP API: creates one browser session and reuses it for every request in the run, hands back each cleared page's HTML/cookies/user-agent, and downloads the actual `.apk`/`.apkm` file afterwards with a plain HTTP client replaying those cookies (FlareSolverr itself can only return page content, not a binary download) — to a temp file first, verifying the byte count against `Content-Length` and that the result actually opens as a valid archive before renaming it into place, with its own short retry on top of that, so a stalled connection or a corrupt/truncated download doesn't need a full Cloudflare re-clear to recover from. The saved file is named `<app>.apk` or `<app>.apkm` from its actual contents (plain APK vs. split bundle), because the patcher CLI decides whether to merge splits from the file extension alone. |
| `github_app.py` | Downloads apps that are mirrored as a direct GitHub release asset instead, per an app's `apk_source` in `catalog/apps.yaml`. Plain HTTP via `morphe_builder/http.py`, no browser or FlareSolverr involved. |
| `release_assets.py` | `download_latest_github_asset()` — fetch a GitHub repo's latest release (or, in prerelease mode, the newest non-draft release that actually ships an asset matching the predicate, so companion releases such as theme-preview zips are skipped), pick that asset, and resumably download it with retries. Used for the patcher jar, every patch bundle (per `catalog/patch_sources.yaml`), and the MicroG/PotHelper companions. Deliberately *not* named `github_something.py` like its neighbor `github_app.py` - that one fetches a specific **app**, this one fetches **any** matching release asset from **any** repo; keeping them named differently keeps that difference obvious at a glance. |

### `scripts/` — entry points

Each of these inserts the repo root onto `sys.path` before importing `morphe_builder` (a sibling directory, not an installed package), so run them from the repo root, e.g. `python scripts/patch.py`.

| File | Purpose |
|---|---|
| `patch.py` | The `patch` job's entry point. For one build: works out which version to fetch (a forced version, the patcher CLI's own `list-versions` output, or APKMirror's latest listing), downloads it, verifies its certificate, patches it, and copies the result into `dist/`. Also fetches the shared `morphe-desktop` patcher jar and every distinct patch bundle the selected builds need, once, before processing any of them. Exits non-zero if any build failed, after attempting all of them. |
| `prepare_release.py` | Runs in the `prepare` job. Validates the catalog, computes a release tag/name for this run, and writes them plus the build matrix as step outputs (`tag`, `name`, `matrix`) for the later jobs to consume. |
| `finalize_release.py` | Runs in the `finalize` job. Scans the downloaded artifacts directory, matches each `.apk` filename back to a build (`match_asset`), builds the release body (icons, versions, per-source collapsible changelogs), creates the GitHub Release, uploads every matched APK plus MicroG/PotHelper if relevant, deletes older releases, and sends the notification. |
| `commit_signature.py` | Runs at the end of every `patch` matrix job (`if: always()`). Resolves the build key it's given back to its owning app slug, then commits any new entry that app's run added to `signatures/known_signatures.json` / `signatures/pending_signatures.json` straight back to `main`, retrying on push conflicts since multiple matrix jobs commit in parallel. |

### `.github/workflows/`

| File | Purpose |
|---|---|
| `patch.yml` | The pipeline itself — `prepare` → `patch` (matrix) → `finalize` → `cleanup`, as described in [How It Works](#how-it-works). |
| `lint.yml` | Runs `ruff check`, `ruff format --check`, `mypy`, and `pytest` on every push to `main` and every pull request. |
| `dependabot.yml` | Automated PRs for GitHub Actions version bumps (daily) and Python package bumps (weekly, `requirements.txt`/`requirements-dev.txt`) — lint-and-test gates every PR, and Dependabot's own PRs auto-merge once it's green. |

### `signatures/`

| File | Purpose |
|---|---|
| `known_signatures.json` | App slug → pinned SHA-256 fingerprint of that app's **original**, pre-patch signing certificate. Edited by hand after manually verifying a new app's fingerprint (see [First run: pending signatures](#4-first-run-pending-signatures)). |
| `pending_signatures.json` | Same shape, auto-populated with fingerprints seen for apps that have no entry in `known_signatures.json` yet. Not trusted for anything — it's a to-review queue. |

### Other files

| File | Purpose |
|---|---|
| `pyproject.toml` | `ruff` (line length 115, `E`/`F`/`I`/`UP`/`B`/`SIM` rule sets), `mypy`, and `pytest` configuration. |
| `requirements.txt` | Direct dependencies, pinned to exact versions. Dependabot opens a PR when one has an update. |
| `requirements-dev.txt` | The above plus `pytest`, `pytest-asyncio`, `ruff`, `mypy`. |
| `requirements-lock.txt` | Full pinned dependency tree (`pip freeze`), installed as-is by both the `prepare` and `patch` jobs. Regenerate it by hand (`pip install -r requirements.txt && pip freeze > requirements-lock.txt`) after a dependency bump lands, and commit it normally — it's no longer touched automatically. |
| `tests/` | `pytest` unit tests for `morphe_builder/retry.py`, `morphe_builder/validate.py`, `morphe_builder/catalog.py`'s app/build merge logic, `morphe_builder/notify.py`, `morphe_builder/apk/versions.py`, `morphe_builder/apk/patcher.py` (including that secrets never leak into its logged command line), `morphe_builder/apk/verify.py`'s pending/known signature flow, `morphe_builder/fetchers/apkmirror.py`'s HTML-parsing/selection logic, `morphe_builder/fetchers/flaresolverr.py`'s download validation, `morphe_builder/fetchers/release_assets.py`'s release-selection logic, and `scripts/finalize_release.py`'s asset-matching logic. |
| `.gitignore` | Excludes `__pycache__`, virtualenvs, downloaded APKs, decoded `.keystore` files, `.env` files, and `diagnostics/`. |

## CI (Lint & Test)

`.github/workflows/lint.yml` runs on every push to `main` and every PR:

```bash
ruff check .
ruff format --check .
mypy morphe_builder scripts
pytest -v
```

Run the same commands locally (`pip install -r requirements-dev.txt` first) before pushing.

## Configuration Reference

Every environment variable `morphe_builder/settings.py` reads (matched case-insensitively). The `patch.yml`/`lint.yml` column shows how the live workflow supplies it; anything not listed there is either optional or only needed for a local run.

| Variable | Used by | Notes |
|---|---|---|
| `GITHUB_TOKEN` | `scripts/finalize_release.py`, `morphe_builder/release.py`, `scripts/commit_signature.py`'s git push, `morphe_builder/fetchers/github_app.py` | Provided automatically by Actions (`secrets.GITHUB_TOKEN`). |
| `GITHUB_REPOSITORY` | `morphe_builder/release.py` | `owner/repo`, set automatically by Actions as `github.repository`. |
| `TARGET_APP` | `scripts/patch.py` | A build key to process just that one build; `all` (default) processes every build in `catalog/apps.yaml`. The `patch` job sets this to `matrix.app`. |
| `FLARESOLVERR_URL` | `morphe_builder/fetchers/flaresolverr.py` | The FlareSolverr instance's API endpoint. Defaults to `http://localhost:8191/v1`, matching the `patch` job's `services:` sidecar container — only override for local runs against a differently-hosted instance. |
| `FLARESOLVERR_TIMEOUT` | `morphe_builder/fetchers/flaresolverr.py` | Seconds to let FlareSolverr spend clearing a single page (its own `maxTimeout`). Defaults to `60`. |
| `DOWNLOAD_TIMEOUT` | `morphe_builder/fetchers/flaresolverr.py` | Seconds to let one app-file download run before giving up (separate from `FLARESOLVERR_TIMEOUT`, which only covers clearing a page). Defaults to `120`. |
| `UPLOAD_CONCURRENCY` | `morphe_builder/release.py` | How many release assets `scripts/finalize_release.py` uploads to GitHub at once, instead of one at a time. Defaults to `6`. |
| `KS_PATH` | `morphe_builder/apk/patcher.py` | Path to the decoded keystore file; the workflow sets this to `release.keystore` (where the "Setup Keystore" step decodes `KEYSTORE_BASE64` to). |
| `KS_PASSWORD` | `morphe_builder/apk/patcher.py` | From the `KEYSTORE_PASSWORD` secret. |
| `KS_ALIAS` | `morphe_builder/apk/patcher.py` | From the `KEY_ALIAS` secret. |
| `KEY_PASSWORD` | `morphe_builder/apk/patcher.py` | From the `KEY_PASSWORD` secret. |
| `SKIP_SIGNATURE_VERIFY` | `morphe_builder/apk/verify.py` | Set to skip the pre-patch certificate pin check entirely. Not set by the workflow; local-use escape hatch only. |
| `KNOWN_SIGNATURES_PATH` / `PENDING_SIGNATURES_PATH` | `morphe_builder/apk/verify.py`, `scripts/commit_signature.py` | Default to `signatures/known_signatures.json` / `signatures/pending_signatures.json`; override only for local testing against a different file. |
| `APPS_CATALOG_PATH` / `PATCH_SOURCES_CATALOG_PATH` | `morphe_builder/catalog.py` | Default to `catalog/apps.yaml` / `catalog/patch_sources.yaml`; override only for local testing against a different catalog. |
| `DISCORD_WEBHOOK_URL` | `morphe_builder/notify.py` | Optional. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | `morphe_builder/notify.py` | Optional, used together. |
| `APPRISE_URLS` | `morphe_builder/notify.py` | Optional, any other Apprise target(s). |
| `RELEASE_TAG` / `RELEASE_NAME` | `scripts/finalize_release.py` | Set by the `finalize` job from `prepare`'s step outputs; required for that script to run at all. |
| `ARTIFACTS_DIR` | `scripts/finalize_release.py` | Where downloaded artifacts land; the workflow sets this to `artifacts`. |
| `NO_COLOR` | `morphe_builder/log.py` | Set (to anything, including empty) to disable colored console output, per the [no-color.org](https://no-color.org) convention. |
| `GITHUB_ACTIONS` | `morphe_builder/log.py` | Set automatically by Actions; switches on GitHub Actions annotation output for warnings/errors. |

## License

[GPL-3.0](LICENSE). If you fork or redistribute this (or a modified version), it stays under the same license.
