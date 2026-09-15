# Güvenlik

- Keystore dosyasını ve parolaları asla repoya eklemeyin. GitHub Environment secret kullanın.
- `REQUIRE_CUSTOM_KEYSTORE=true` ile release build'lerinde testkey fallback'i kapatın.
- `data/known_signatures.json` pin'lerini yalnızca resmi kaynakla doğruladıktan sonra güncelleyin.
- `data/pinned_assets.json` içine SHA-256 eklerken upstream release tag'ini ve dosya adını manuel doğrulayın.
- Eski release silme politikası için `RELEASE_KEEP_LATEST>=2` önerilir.

Şüpheli bir signature mismatch veya supply-chain şüphesi için workflow'u durdurup
release'leri draft/retained durumda inceleyin.
