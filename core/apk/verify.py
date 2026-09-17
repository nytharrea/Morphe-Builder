"""İmza ve sağlama toplamı doğrulama araçları."""

import hashlib
import json
import os


def calculate_sha256(file_path: str) -> str:
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def verify_signature(file_path: str, known_db_path: str, package_name: str) -> bool:
    if not os.path.exists(known_db_path):
        return True
    with open(known_db_path, "r", encoding="utf-8") as f:
        signatures = json.load(f)
    expected = signatures.get(package_name)
    if not expected:
        return True
    return calculate_sha256(file_path) == expected
