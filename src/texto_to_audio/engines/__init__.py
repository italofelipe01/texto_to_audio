"""Engine registry and automatic fallback chain.

``auto`` tries the best free engines first and silently falls back when one is
unavailable (no internet, missing package...): Edge neural → Piper offline →
gTTS → eSpeak.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import EngineError, EngineUnavailable, SynthesisResult, TTSEngine, Voice
from .edge import EdgeEngine
from .espeak import EspeakEngine
from .gtts_engine import GTTSEngine
from .piper_engine import PiperEngine

if TYPE_CHECKING:
    from ..config import Settings

__all__ = [
    "EngineError",
    "EngineUnavailable",
    "SynthesisResult",
    "TTSEngine",
    "Voice",
    "FALLBACK_ORDER",
    "register_engine",
    "engine_names",
    "get_engine",
    "engine_chain",
    "find_voice_engine",
    "unregister_engine",
]

FALLBACK_ORDER = ["edge", "piper", "gtts", "espeak"]

_REGISTRY: dict[str, type[TTSEngine]] = {}
# (id(settings), name) -> (settings, engine); the settings reference guards against id reuse
_INSTANCES: dict[tuple[int, str], tuple[Settings, TTSEngine]] = {}


def register_engine(cls: type[TTSEngine], *, priority: int | None = None) -> type[TTSEngine]:
    _REGISTRY[cls.name] = cls
    if cls.name not in FALLBACK_ORDER:
        FALLBACK_ORDER.insert(len(FALLBACK_ORDER) if priority is None else priority, cls.name)
    _INSTANCES.clear()
    return cls


def unregister_engine(name: str) -> None:
    _REGISTRY.pop(name, None)
    if name in FALLBACK_ORDER:
        FALLBACK_ORDER.remove(name)
    _INSTANCES.clear()


for _cls in (EdgeEngine, PiperEngine, GTTSEngine, EspeakEngine):
    register_engine(_cls)


def engine_names() -> list[str]:
    return [n for n in FALLBACK_ORDER if n in _REGISTRY]


def get_engine(name: str, settings: Settings) -> TTSEngine:
    if name not in _REGISTRY:
        raise EngineUnavailable(
            f"Motor de voz desconhecido: '{name}'. Opções: auto, {', '.join(engine_names())}"
        )
    key = (id(settings), name)
    cached = _INSTANCES.get(key)
    if cached is None or cached[0] is not settings:
        cached = (settings, _REGISTRY[name](settings))
        _INSTANCES[key] = cached
    return cached[1]


def engine_chain(preferred: str, settings: Settings, *, fallback: bool = True) -> list[TTSEngine]:
    """Ordered list of available engines to try for a job."""
    names = engine_names()
    if preferred and preferred != "auto":
        if preferred not in _REGISTRY:
            get_engine(preferred, settings)  # raises a helpful error
        names = [preferred] + ([n for n in names if n != preferred] if fallback else [])
    chain = []
    for name in names:
        engine = get_engine(name, settings)
        ok, _reason = engine.check()
        if ok:
            chain.append(engine)
    return chain


def find_voice_engine(voice: str, settings: Settings) -> str | None:
    """Guess which engine owns a voice id (used when the user only gives a voice)."""
    from .espeak import VOICES as ESPEAK_VOICES
    from .gtts_engine import ACCENTS

    if voice.endswith("Neural"):
        return "edge"
    if voice.endswith(".onnx") or re.fullmatch(
        r"[a-z]{2,3}_[A-Z]{2}-.+-(x_low|low|medium|high)", voice
    ):
        return "piper"
    if voice in ACCENTS:
        return "gtts"
    if voice in ESPEAK_VOICES:
        return "espeak"
    for name in engine_names():
        engine = get_engine(name, settings)
        if (
            not engine.online
            and engine.check()[0]
            and any(v.id == voice for v in engine.list_voices())
        ):
            return name
    return None
