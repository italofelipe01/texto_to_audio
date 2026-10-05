"""Common interface implemented by every TTS engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from ..config import Settings


class EngineError(RuntimeError):
    """Synthesis failed (network error, invalid voice, ...)."""


class EngineUnavailable(EngineError):
    """The engine cannot run on this machine (missing package, binary or model)."""


@dataclass
class Voice:
    id: str
    name: str
    locale: str
    engine: str
    gender: str | None = None
    quality: str | None = None
    installed: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SynthesisResult:
    path: Path
    # (start_seconds, end_seconds, text) for each sentence, when the engine reports them
    sentences: list[tuple[float, float, str]] = field(default_factory=list)


def locale_matches(voice_locale: str, wanted: str | None) -> bool:
    """``pt`` matches ``pt-BR`` and ``pt-PT``; ``pt-BR`` matches only ``pt-BR``."""
    if not wanted:
        return True
    voice_locale = voice_locale.lower().replace("_", "-")
    wanted = wanted.lower().replace("_", "-")
    return voice_locale == wanted or voice_locale.startswith(wanted + "-")


class TTSEngine(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str] = ""
    online: ClassVar[bool] = False
    native_rate: ClassVar[bool] = False
    output_ext: ClassVar[str] = "wav"
    max_concurrency: ClassVar[int] = 4

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    def check(self) -> tuple[bool, str]:
        """Return ``(available, reason)`` without doing network calls."""

    @abstractmethod
    def list_voices(self, locale: str | None = None) -> list[Voice]: ...

    @abstractmethod
    def default_voice(self, locale: str) -> str | None: ...

    @abstractmethod
    def synthesize(self, text: str, voice: str, out_path: Path, rate: int = 0) -> SynthesisResult:
        """Write audio for ``text`` to ``out_path``; ``rate`` is a speed change in percent."""

    def signature(self) -> str:
        """Identifies the engine version in cache keys."""
        return self.name

    def info(self) -> dict:
        ok, reason = self.check()
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "online": self.online,
            "native_rate": self.native_rate,
            "offline": not self.online,
            "available": ok,
            "reason": reason,
        }
