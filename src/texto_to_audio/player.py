"""Local playback through ``ffplay`` (ships with FFmpeg)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def play(path: str | Path) -> bool:
    """Play an audio file without opening a window. Returns ``True`` on success."""
    path = Path(path)
    if not path.exists():
        logger.error("Arquivo não encontrado: %s", path)
        return False
    ffplay = shutil.which("ffplay")
    if not ffplay:
        logger.error("ffplay não encontrado no PATH. Instale o FFmpeg completo para reproduzir.")
        return False
    logger.info("Reproduzindo %s (Ctrl+C para parar)", path)
    try:
        result = subprocess.run(
            [ffplay, "-autoexit", "-nodisp", "-loglevel", "quiet", str(path)], check=False
        )
    except KeyboardInterrupt:
        return True
    except OSError as exc:
        logger.error("Erro ao reproduzir: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning("ffplay terminou com código %s", result.returncode)
        return False
    return True
