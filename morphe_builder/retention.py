from __future__ import annotations

from datetime import datetime
from typing import Any


def _created_at(release: dict[str, Any]) -> datetime:
    raw = release.get("created_at") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.fromtimestamp(0).astimezone()


def select_releases_to_delete(releases: list[dict], keep_release_id: int, keep_latest: int = 3) -> list[dict]:
    """Select old releases to delete while keeping the newest N releases.

    keep_latest <= 0 preserves the historical behaviour of deleting every
    release except the current one. Positive values keep a safety window.
    """
    unique = {release["id"]: release for release in releases if "id" in release}
    ordered = sorted(unique.values(), key=_created_at, reverse=True)
    if keep_latest <= 0:
        return [release for release in ordered if release["id"] != keep_release_id]
    keep_ids = {release["id"] for release in ordered[:keep_latest]}
    keep_ids.add(keep_release_id)
    return [release for release in ordered if release["id"] not in keep_ids]
