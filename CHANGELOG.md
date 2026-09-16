# Changelog

## 2026-09-16
- APKMirror kaynağı Camoufox/Playwright'tan FlareSolverr servisine taşındı.
- `core/flaresolverr.py` eklendi; workflow `patch` job'una FlareSolverr service eklendi.
- `requirements.txt`/lock'tan Camoufox ve Playwright kaldırıldı.

## 2026-09-14

- Build manifest üretimi ve finalize manifest öncelikli eşleşme.
- GitHub asset SHA-256 pin desteği (`data/pinned_assets.json`).
- Release retention politikası (`RELEASE_KEEP_LATEST`).
- Custom keystore strict modu (`REQUIRE_CUSTOM_KEYSTORE`).
- `latest_compatible` version policy seçeneği.
- Atomic JSON yazımı ve temel `doctor` komutu.
- Workflow'a concurrency, keystore chmod/umask ve manifest artifact aktarımı eklendi.