from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .hashes import sha256_file
from .json_atomic import save_json_atomic

MANIFEST_NAME = "build-manifest.json"
MANIFEST_GLOB = "build-manifest*.json"


def manifest_path(dist_dir: str | Path) -> Path:
    return Path(dist_dir) / MANIFEST_NAME


def write_build_manifest(
    dist_dir: str | Path,
    apps: list[dict],
    tools: dict | None = None,
    run_key: str = "all",
) -> Path:
    dist = Path(dist_dir)
    dist.mkdir(parents=True, exist_ok=True)
    normalized_apps = []
    for app in apps:
        name = app.get("name")
        normalized_apps.append(
            {
                "app_key": app.get("app_key"),
                "app_name": app.get("app_name"),
                "pkg": app.get("pkg"),
                "display_name": app.get("display_name"),
                "version": app.get("version"),
                "name": name,
                "sha256": app.get("sha256")
                or (sha256_file(dist / name) if name and (dist / name).exists() else None),
                "patch_source": app.get("patch_source"),
                "patch_sources": app.get("patch_sources") or [],
                "source_signatures": app.get("source_signatures") or [],
                "icon": app.get("icon"),
            }
        )
    payload = {
        "schema": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "tools": tools or {},
        "apps": normalized_apps,
    }
    file_name = MANIFEST_NAME if run_key in (None, "", "all") else f"build-manifest-{run_key}.json"
    return save_json_atomic(dist / file_name, payload)


def load_build_manifest(artifacts_dir: str | Path) -> dict | None:
    path = Path(artifacts_dir) / MANIFEST_NAME
    if not path.exists():
        return None
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        return None
    return data


def load_build_manifests(artifacts_dir: str | Path) -> dict | None:
    root = Path(artifacts_dir)
    merged: dict = {"schema": 1, "generated_at": None, "tools": {}, "apps": []}
    found = False
    for path in sorted(root.glob(MANIFEST_GLOB)):
        import json

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
            continue
        found = True
        merged["apps"].extend(data["apps"])
        if data.get("generated_at") and not merged["generated_at"]:
            merged["generated_at"] = data["generated_at"]
        tools = data.get("tools")
        if isinstance(tools, dict):
            merged["tools"].update(tools)
    if not found:
        return None
    # De-duplicate by app_key, keeping the last entry.
    unique = {app.get("app_key"): app for app in merged["apps"] if app.get("app_key")}
    merged["apps"] = list(unique.values())
    return merged
