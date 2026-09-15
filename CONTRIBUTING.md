# Katkı

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy .
pytest -q
```

Yeni mantık eklerken mevcut davranışı varsayılan olarak koruyun; sıkılaştırmalar
environment flag arkasında olsun. APKMirror HTML parse değişikliklerinde fixture
testi ekleyin.
