"""Builder-Morphe ana derleme ve çalıştırma betiği."""

import logging
import os
import sys

from core.apk.patcher import apply_patches
from core.config import APPLICATIONS
from core.http import NetworkSessionManager
from core.settings import BUILD_DIR, FLARESOLVERR_URL
from core.sources.apkmirror import APKMirrorSourceProvider
from core.sources.github_apk import GitHubSourceProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("morphe.main")


def run_pipeline(app_name: str) -> None:
    if app_name not in APPLICATIONS:
        raise ValueError(f"Bilinmeyen hedef uygulama: {app_name}. Tanımlı: {list(APPLICATIONS.keys())}")

    app_config = APPLICATIONS[app_name]
    logger.info("=== [%s] Yamalama Süreci Başlatıldı ===", app_name)

    app_build_dir = os.path.join(BUILD_DIR, app_name)
    os.makedirs(app_build_dir, exist_ok=True)
    raw_apk_path = os.path.join(app_build_dir, f"{app_name}-source.apk")
    output_apk_path = os.path.join(app_build_dir, app_config["output_apk_name"])

    session_mgr = NetworkSessionManager(solver_endpoint=FLARESOLVERR_URL)

    if app_config["source_provider"] == "apkmirror":
        provider = APKMirrorSourceProvider(session_mgr)
        cfg = app_config["provider_config"]
        direct_url = provider.resolve_apk_download(
            org=cfg["org"],
            app_slug=cfg["app_slug"],
            target_arch=cfg.get("arch", "arm64-v8a"),
            target_dpi=cfg.get("dpi", "nodpi"),
            version=cfg.get("version"),
        )
        session_mgr.download_file(direct_url, raw_apk_path)
    elif app_config["source_provider"] == "github_apk":
        provider = GitHubSourceProvider()
        cfg = app_config["provider_config"]
        download_url = provider.get_release_asset_url(cfg["repo"], cfg["asset_pattern"])
        session_mgr.download_file(download_url, raw_apk_path)
    else:
        raise NotImplementedError(f"Desteklenmeyen sağlayıcı: {app_config['source_provider']}")

    logger.info("Ham APK hazırlandı: %s", raw_apk_path)
    apply_patches(raw_apk_path, output_apk_path, app_config["patches"])
    logger.info("=== [%s] İşlemi Tamamlandı ===", app_name)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "instagram"
    run_pipeline(target)
