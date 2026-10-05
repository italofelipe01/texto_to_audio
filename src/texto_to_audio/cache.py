"""Content-addressed cache of synthesized chunks.

Re-running a job (or a job that failed half way) only synthesizes the chunks
that changed, which saves time and avoids hammering free online services.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class SynthCache:
    def __init__(self, root: Path, max_mb: int = 2048) -> None:
        self.root = Path(root)
        self.max_bytes = max(0, max_mb) * 1024 * 1024
        self._lock = threading.Lock()

    @staticmethod
    def key(*parts: object) -> str:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(repr(part).encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def _paths(self, key: str, ext: str) -> tuple[Path, Path]:
        folder = self.root / key[:2]
        return folder / f"{key}.{ext}", folder / f"{key}.json"

    def get(self, key: str, ext: str) -> tuple[Path, dict] | None:
        audio, meta = self._paths(key, ext)
        if not audio.exists() or audio.stat().st_size == 0:
            return None
        data: dict = {}
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        with contextlib.suppress(OSError):
            os.utime(audio)  # LRU bookkeeping for prune()
        return audio, data

    def put(self, key: str, ext: str, src: Path, meta: dict | None = None) -> Path:
        audio, meta_path = self._paths(key, ext)
        audio.parent.mkdir(parents=True, exist_ok=True)
        tmp = audio.with_suffix(f".{ext}.tmp{threading.get_ident()}")
        shutil.copyfile(src, tmp)
        os.replace(tmp, audio)
        if meta:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return audio

    def size(self) -> int:
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def prune(self) -> int:
        """Delete least recently used entries until the cache fits ``max_mb``."""
        if not self.max_bytes or not self.root.exists():
            return 0
        with self._lock:
            files = [p for p in self.root.rglob("*") if p.is_file() and p.suffix != ".json"]
            total = sum(p.stat().st_size for p in files)
            removed = 0
            for path in sorted(files, key=lambda p: p.stat().st_mtime):
                if total <= self.max_bytes:
                    break
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
                path.with_suffix(".json").unlink(missing_ok=True)
                removed += 1
            if removed:
                logger.info("Cache: %d itens antigos removidos", removed)
            return removed

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
