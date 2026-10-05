"""Runtime settings, read from environment variables (prefix ``TTA_``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"TTA_{name}")
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


@dataclass
class Settings:
    """Global configuration shared by the CLI, the watcher and the web server."""

    data_dir: Path = field(default_factory=lambda: Path("data"))
    output_dir: Path | None = None
    cache_dir: Path | None = None
    piper_voices_dirs: list[Path] = field(default_factory=list)
    piper_auto_download: bool = True
    default_engine: str = "auto"
    default_lang: str = "pt-BR"
    max_workers: int = 4
    max_jobs: int = 2
    max_chars: int = 500_000
    max_upload_mb: int = 50
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    proxy: str | None = None
    job_ttl_days: int = 7
    cache_max_mb: int = 2048
    lexicon_file: Path | None = None
    ca_bundle: Path | None = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.output_dir is None:
            self.output_dir = self.data_dir / "saida"
        if self.cache_dir is None:
            self.cache_dir = self.data_dir / "cache"
        if not self.piper_voices_dirs:
            self.piper_voices_dirs = [self.data_dir / "piper-voices"]

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(_env("DATA_DIR", "data"))
        piper_dirs = _env("PIPER_VOICES_DIR")
        lexicon = _env("LEXICON")
        output = _env("OUTPUT_DIR")
        cache = _env("CACHE_DIR")
        return cls(
            data_dir=data_dir,
            output_dir=Path(output) if output else None,
            cache_dir=Path(cache) if cache else None,
            piper_voices_dirs=[Path(p) for p in piper_dirs.split(os.pathsep) if p]
            if piper_dirs
            else [],
            piper_auto_download=_env_bool("PIPER_AUTO_DOWNLOAD", True),
            default_engine=_env("ENGINE", "auto"),
            default_lang=_env("LANG", "pt-BR"),
            max_workers=max(1, _env_int("MAX_WORKERS", 4)),
            max_jobs=max(1, _env_int("MAX_JOBS", 2)),
            max_chars=_env_int("MAX_CHARS", 500_000),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 50),
            ffmpeg_bin=_env("FFMPEG", "ffmpeg"),
            ffprobe_bin=_env("FFPROBE", "ffprobe"),
            proxy=_env("PROXY"),
            job_ttl_days=_env_int("JOB_TTL_DAYS", 7),
            cache_max_mb=_env_int("CACHE_MAX_MB", 2048),
            lexicon_file=Path(lexicon) if lexicon else None,
            ca_bundle=Path(_env("CA_BUNDLE")) if _env("CA_BUNDLE") else None,
        )
