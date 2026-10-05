"""Hands-free mode: watch a folder and convert every document dropped into it.

Layout::

    entrada/                 <- drop .txt/.md/.docx/.pdf/.epub/... here
    entrada/livro.json       <- (optional) options for livro.* e.g. {"preset": "audiobook"}
    entrada/processados/     <- sources moved here after success
    entrada/falhas/          <- sources + <name>.erro.txt on failure
    saida/livro/             <- generated audio, subtitles, video, report
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path

from .pipeline import Pipeline, PipelineError
from .presets import JobOptions
from .text import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

DONE_DIR = "processados"
FAILED_DIR = "falhas"
ASSET_KEYS = ("music", "intro", "outro", "cover", "background")


def _sidecar_options(source: Path) -> tuple[dict, Path | None]:
    sidecar = source.with_suffix(".json")
    if not sidecar.exists():
        return {}, None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Arquivo de opções inválido ({sidecar.name}): {exc}") from exc
    if not isinstance(data, dict):
        raise PipelineError(f"{sidecar.name} deve conter um objeto JSON")
    for key in ASSET_KEYS:
        if data.get(key):
            asset = Path(data[key])
            data[key] = str(asset if asset.is_absolute() else source.parent / asset)
    return data, sidecar


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        target = folder / f"{path.stem}-{int(time.time())}{path.suffix}"
    shutil.move(str(path), target)
    return target


def process_file(source: Path, output_root: Path, base: dict, pipeline: Pipeline) -> bool:
    inbox = source.parent
    sidecar = None
    try:
        extra, sidecar = _sidecar_options(source)
        options = JobOptions.from_dict({**base, **extra})
        output_dir = output_root / source.stem
        logger.info("Processando %s → %s", source.name, output_dir)
        result = pipeline.run(options=options, output_dir=output_dir, source=source)
        logger.info("Concluído: %s (%.1fs de áudio)", source.name, result.report["duration"])
        _move(source, inbox / DONE_DIR)
        if sidecar:
            _move(sidecar, inbox / DONE_DIR)
        return True
    except Exception as exc:  # keep watching whatever happens with one file
        logger.error("Falha em %s: %s", source.name, exc)
        failed = _move(source, inbox / FAILED_DIR)
        failed.with_name(failed.name + ".erro.txt").write_text(str(exc), encoding="utf-8")
        if sidecar and sidecar.exists():
            _move(sidecar, inbox / FAILED_DIR)
        return False


def watch(
    inbox: Path,
    output_root: Path,
    base_options: dict,
    pipeline: Pipeline,
    *,
    interval: float = 5.0,
    once: bool = False,
    stop: threading.Event | None = None,
) -> int:
    """Poll ``inbox`` and process new documents. Returns how many were processed."""
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    output_root = Path(output_root)
    stop = stop or threading.Event()
    sizes: dict[Path, int] = {}
    processed = 0
    if not once:
        logger.info("Monitorando %s (Ctrl+C para sair). Saída em %s", inbox, output_root)
    while not stop.is_set():
        candidates = sorted(
            p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        for path in candidates:
            if stop.is_set():
                break
            size = path.stat().st_size
            stable = once or (sizes.get(path) == size and time.time() - path.stat().st_mtime > 1.5)
            sizes[path] = size
            if not stable:
                continue
            sizes.pop(path, None)
            process_file(path, output_root, base_options, pipeline)
            processed += 1
        if once:
            break
        stop.wait(interval)
    return processed
