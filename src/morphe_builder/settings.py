"""Centralized runtime configuration, backed by pydantic-settings.

Every environment variable the pipeline reads is declared here once, with
its type and default, instead of being scattered as ad-hoc os.environ.get()
calls across a dozen modules. Env vars are matched case-insensitively
(KS_PATH, ks_path, Ks_Path all bind to `ks_path`), so every existing GitHub
Actions secret/env name keeps working unchanged.

One shared `settings` singleton is imported by both the morphe_builder/
library code and the top-level scripts (scripts/patch.py,
scripts/prepare_release.py, scripts/finalize_release.py,
scripts/commit_signature.py) - and those scripts each only need a subset of
these fields, so nothing here is a required field. Anything that's truly
required for a given entrypoint (e.g. RELEASE_TAG for
scripts/finalize_release.py) is checked explicitly at the point of use
instead, the same way the original code did with plain os.environ lookups.
"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import paths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    github_token: SecretStr = SecretStr("")
    github_repository: str = ""

    target_app: str = "all"

    ks_path: Path | None = None
    ks_password: SecretStr | None = None
    ks_alias: str | None = None
    key_password: SecretStr | None = None

    flaresolverr_url: str = "http://localhost:8191/v1"
    flaresolverr_timeout: float = 60.0
    download_timeout: float = 120.0
    patch_timeout: float = 300.0  # seconds of complete silence from the patch CLI before patcher.py kills it

    skip_signature_verify: bool = False
    known_signatures_path: Path = Field(
        default_factory=lambda: paths.repo_root() / "signatures" / "known_signatures.json"
    )
    pending_signatures_path: Path = Field(
        default_factory=lambda: paths.repo_root() / "signatures" / "pending_signatures.json"
    )

    apps_catalog_path: Path = Field(default_factory=lambda: paths.repo_root() / "catalog" / "apps.yaml")
    patch_sources_catalog_path: Path = Field(
        default_factory=lambda: paths.repo_root() / "catalog" / "patch_sources.yaml"
    )

    discord_webhook_url: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    apprise_urls: SecretStr = SecretStr("")

    no_color: str | None = None
    github_actions: bool = False

    release_tag: str | None = None
    release_name: str | None = None
    artifacts_dir: Path = Path("artifacts")
    upload_concurrency: int = 6


settings = Settings()
