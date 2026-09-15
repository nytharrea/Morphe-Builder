from __future__ import annotations

import shutil
from pathlib import Path

from ..hashes import sha256_file


class ContentCache:
    """Tiny content-addressed cache used for optional local reuse."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def key_for_file(self, path: str | Path) -> str:
        return sha256_file(path)

    def put(self, key: str, path: str | Path) -> Path:
        dest = self.root / key
        if not dest.exists():
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            shutil.copyfile(path, tmp)
            tmp.replace(dest)
        return dest

    def get(self, key: str) -> Path | None:
        path = self.root / key
        return path if path.exists() else None
