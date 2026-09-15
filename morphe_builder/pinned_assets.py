from __future__ import annotations

from pathlib import Path
from typing import Any

from .exceptions import AssetPinMismatch
from .json_atomic import load_json

_MISSING = object()


def _normalize_pins(raw: Any) -> dict[str, str]:
    """Return normalized pins keyed by owner/repo/asset-name.

    Supported shapes:
      {"owner/repo": "sha256"}
      {"owner/repo": {"asset": "name.jar", "sha256": "..."}}
      {"owner/repo/asset.jar": "sha256"}
    """
    pins: dict[str, str] = {}
    if not isinstance(raw, dict):
        return pins
    for key, value in raw.items():
        if isinstance(value, str):
            pins[str(key)] = value.lower()
        elif isinstance(value, dict):
            asset = value.get("asset") or value.get("name")
            sha = value.get("sha256") or value.get("sha256sum")
            if asset and sha:
                pins[f"{key}/{asset}"] = str(sha).lower()
    return pins


def verify_asset_pin(owner: str, repo: str, asset_name: str, sha256: str, path: str | Path) -> bool:
    """Verify an asset against data/pinned_assets.json when a pin exists.

    Missing pin files or missing entries are intentionally non-fatal so this
    does not change version selection. Add a pin only after manually checking
    the upstream release.
    """
    raw = load_json(path, default={})
    pins = _normalize_pins(raw)
    key = f"{owner}/{repo}/{asset_name}"
    expected = pins.get(key, _MISSING)
    if expected is _MISSING:
        return False
    actual = sha256.lower()
    if actual != expected:
        raise AssetPinMismatch(f"Pinned SHA-256 mismatch for {key}: expected {expected}, got {actual}")
    return True
