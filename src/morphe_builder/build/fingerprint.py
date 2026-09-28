"""Deterministic config fingerprint for a single build.

Anything that would change the produced APK (without changing app or patch
version numbers) must be included here: patch_sources list, exclude/enable
lists, options, arch, force_version, force_build.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from morphe_builder.catalog import BuildConfig


def _normalize(value: Any) -> Any:
    """Make nested structures order-stable for hashing."""
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)


def config_fingerprint(build: BuildConfig) -> str:
    """Return a short hex digest of the build configuration that affects output.

    Changing any of these fields must force a rebuild even when app version
    and patch tags are unchanged.
    """
    payload = {
        "patch_sources": list(build.get("patch_sources") or []),
        "exclude": list(build.get("exclude") or []),
        "enable": list(build.get("enable") or []),
        "options": dict(build.get("options") or {}),
        "arch": build.get("arch"),
        "force_version": build.get("force_version"),
        "force_build": build.get("force_build"),
        "pkg": build.get("pkg"),
        "apk_source": dict(build.get("apk_source") or {}),
    }
    normalized = _normalize(payload)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
