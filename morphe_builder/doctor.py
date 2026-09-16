from __future__ import annotations

import shutil
from pathlib import Path

from core.settings import settings


def check_environment() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    add("java", shutil.which("java") is not None, shutil.which("java") or "not found")
    add("GITHUB_TOKEN", bool(settings.github_token.get_secret_value()), "")
    add("GITHUB_REPOSITORY", bool(settings.github_repository), settings.github_repository)
    add("TARGET_APP", bool(settings.target_app), settings.target_app)
    add("ks_path", settings.ks_path is not None and settings.ks_path.exists(), str(settings.ks_path or "missing"))
    add("ks_password", bool(settings.ks_password and settings.ks_password.get_secret_value()), "")
    add("ks_alias", bool(settings.ks_alias), settings.ks_alias)
    add("key_password", bool(settings.key_password and settings.key_password.get_secret_value()), "")
    add("known_signatures_path", settings.known_signatures_path.exists(), str(settings.known_signatures_path))
    add("artifacts_dir", Path(settings.artifacts_dir).exists(), str(settings.artifacts_dir))
    add("flaresolverr_url", bool(settings.flaresolverr_url), settings.flaresolverr_url)
    return checks


def main() -> int:
    checks = check_environment()
    failed = 0
    for name, ok, detail in checks:
        status = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"{status:4} {name:24} {detail}")
    if settings.require_custom_keystore and not (
        settings.ks_path
        and settings.ks_path.exists()
        and settings.ks_password
        and settings.ks_alias
        and settings.key_password
    ):
        failed += 1
        print("FAIL require_custom_keystore   custom keystore is required but incomplete")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
