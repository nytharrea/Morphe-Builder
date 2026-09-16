# Builder-Morphe

Android APK patching automation pipeline: kaynak sürümü bulur, APK indirir,
kaynak imzasını doğrular, Morphe Desktop ile patch'ler, kendi keystore'unla
imzalar ve tek GitHub release altında yayınlar.

Bu paket; mevcut `core/` yapısını bozmadan eklenen `morphe_builder/`
yardımcı modülleri, manifest/retention/asset-pin desteği, APKMirror için
FlareSolverr altyapısı ve daha güvenli workflow davranışları içerir.
**İmza anahtarın ve MicroG sürüm seçim mantığı değiştirilmedi.**

## Neler eklendi/düzeltildi

- `build-manifest.json`: finalize artık sadece dosya adı tahmini yerine önce manifest kullanır.
- `data/pinned_assets.json`: indirilen desktop/patch asset'leri için isteğe bağlı SHA-256 pin.
- Release retention: `RELEASE_KEEP_LATEST=3` ile son 3 release tutulur; 0 eski “hepsini sil” davranışını verir.
- `REQUIRE_CUSTOM_KEYSTORE=true`: keystore eksikse testkey'e düşmeden job başarısız olur.
- `VERSION_POLICY`: varsayılan mevcut davranış korunur; istersen `latest_compatible` yapılır.
- `python -m morphe_builder.doctor`: temel ortam/secret kontrolü.
- Atomic JSON yazımı, retention seçimi, version policy, rate-limit helper modülleri.
- Notification secret'ları için `.env.example` ve dokümantasyon.
- APKMirror scraping artık Camoufox/Playwright yerine FlareSolverr servisi üzerinden
  çalışır; workflow `patch` job'unda `ghcr.io/flaresolverr/flaresolverr:latest`
  service olarak ayağa kalkar.

## Hızlı kullanım

```bash
python -m pip install -r requirements-lock.txt
python -m morphe_builder.doctor
python prepare_release.py
TARGET_APP=youtube python main.py
ARTIFACTS_DIR=dist RELEASE_TAG=test RELEASE_NAME=test python finalize_release.py
```

## Yeni environment değişkenleri

- `REQUIRE_CUSTOM_KEYSTORE`: `true` ise custom keystore yoksa build fail olur.
- `VERSION_POLICY`: `max_patches_then_version` varsayılan; `latest_compatible` seçeneği eklendi.
- `RELEASE_KEEP_LATEST`: finalize'de tutulacak release sayısı.
- `PINNED_ASSETS_PATH`: asset SHA-256 pin dosyası.
- `FLARESOLVERR_URL`: FlareSolverr servis adresi, varsayılan `http://127.0.0.1:8191`.
- `FLARESOLVERR_MAX_TIMEOUT_MS`: challenge çözme üst sınırı.
- `FLARESOLVERR_SESSION`: APKMirror oturum adı.
- `FLARESOLVERR_RETRIES`: challenge tekrar sayısı.

## Asset pin örneği

`data/pinned_assets.json`:

```json
{
  "MorpheApp/morphe-desktop/morphe-desktop-1.16.0-dev.1-all.jar": "sha256..."
}
```

Pin yoksa davranış değişmez; pin varsa hash uyuşmazlığı build'i durdurur.

## Güvenlik notu

Keystore secret'ları GitHub Secret/Environment üzerinden verilmelidir. Mevcut
Morphe Desktop CLI parolaları komut satırından aldığı için runner'da process
argümanı riski tamamen kalkmaz; bu yüzden keystore fallback'i kapalı tutun,
secret'ları asla repo'ya koymayın ve release job'unda manual approval kullanın.

MicroG/PotHelper companion asset'leri için sürüm seçim mantığı bilinçli olarak
değiştirilmedi; yalnızca isteğe bağlı hash pin desteği eklendi.
