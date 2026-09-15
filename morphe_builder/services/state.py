from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..json_atomic import load_json, save_json_atomic


def record_run(path: str | Path, run: dict[str, Any]) -> Path:
    data = load_json(path, default={"schema": 1, "runs": []})
    if not isinstance(data, dict):
        data = {"schema": 1, "runs": []}
    runs = data.setdefault("runs", [])
    run = dict(run)
    run.setdefault("recorded_at", datetime.now(UTC).isoformat())
    runs.append(run)
    del runs[:-50]
    return save_json_atomic(path, data)
