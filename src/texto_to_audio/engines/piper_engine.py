"""Piper: fast neural TTS that runs 100% offline (``pip install piper-tts``).

Voice models (``*.onnx`` + ``*.onnx.json``) are looked up in
``TTA_PIPER_VOICES_DIR`` and downloaded automatically on first use.
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path

from .base import EngineError, EngineUnavailable, SynthesisResult, TTSEngine, Voice, locale_matches

logger = logging.getLogger(__name__)

# Downloadable voices offered even before they are installed.
KNOWN_VOICES = {
    "pt_BR-faber-medium": "Male",
    "pt_BR-cadu-medium": "Male",
    "pt_BR-jeff-medium": "Male",
    "pt_BR-edresson-low": "Male",
    "en_US-lessac-medium": "Male",
    "en_US-amy-medium": "Female",
    "es_ES-davefx-medium": "Male",
}

DEFAULT_VOICES = {
    "pt": "pt_BR-faber-medium",
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
}


def _locale_of(voice_id: str) -> str:
    return voice_id.split("-", 1)[0].replace("_", "-")


class PiperEngine(TTSEngine):
    name = "piper"
    label = "Piper (offline)"
    description = (
        "Voz neural local, gratuita e privada: funciona sem internet após baixar o modelo (~60 MB)."
    )
    online = False
    native_rate = True
    output_ext = "wav"
    max_concurrency = 1

    _models: dict[Path, object] = {}
    _locks: dict[Path, threading.Lock] = {}
    _global_lock = threading.Lock()

    def check(self) -> tuple[bool, str]:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False, "pacote 'piper-tts' não instalado (pip install texto-to-audio[piper])"
        if self._installed():
            return True, "ok (offline)"
        if self.settings.piper_auto_download:
            return True, "ok (o modelo de voz será baixado no primeiro uso)"
        return False, "nenhum modelo de voz encontrado (use: tta download-voice pt_BR-faber-medium)"

    def _installed(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for folder in self.settings.piper_voices_dirs:
            folder = Path(folder)
            if folder.is_dir():
                for model in sorted(folder.glob("*.onnx")):
                    if model.with_suffix(".onnx.json").exists():
                        found.setdefault(model.stem, model)
        return found

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        installed = self._installed()
        ids = list(installed) + [v for v in KNOWN_VOICES if v not in installed]
        return [
            Voice(
                id=vid,
                name=vid.split("-", 1)[-1].replace("-", " ").title(),
                locale=_locale_of(vid),
                engine=self.name,
                gender=KNOWN_VOICES.get(vid),
                quality="neural-offline",
                installed=vid in installed,
            )
            for vid in ids
            if locale_matches(_locale_of(vid), locale)
        ]

    def default_voice(self, locale: str) -> str | None:
        for vid in self._installed():
            if locale_matches(_locale_of(vid), locale):
                return vid
        return DEFAULT_VOICES.get(locale.lower()[:2])

    def download(self, voice: str) -> Path:
        try:
            from piper.download_voices import download_voice
        except ImportError as exc:
            raise EngineUnavailable("piper-tts não instalado") from exc
        target = Path(self.settings.piper_voices_dirs[0])
        target.mkdir(parents=True, exist_ok=True)
        logger.info("Baixando voz Piper '%s' para %s ...", voice, target)
        try:
            download_voice(voice, target)
        except Exception as exc:
            # no retry: a failed download means no network or an unknown voice
            raise EngineUnavailable(f"Falha ao baixar a voz Piper '{voice}': {exc}") from exc
        return target / f"{voice}.onnx"

    def _model_path(self, voice: str) -> Path:
        candidate = Path(voice)
        if candidate.suffix == ".onnx" and candidate.exists():
            return candidate
        installed = self._installed()
        if voice in installed:
            return installed[voice]
        if not self.settings.piper_auto_download:
            raise EngineUnavailable(f"Modelo Piper '{voice}' não encontrado")
        return self.download(voice)

    def _load(self, model_path: Path):
        with self._global_lock:
            if model_path not in self._models:
                from piper import PiperVoice

                self._models[model_path] = PiperVoice.load(model_path)
                self._locks[model_path] = threading.Lock()
            return self._models[model_path], self._locks[model_path]

    def synthesize(self, text: str, voice: str, out_path: Path, rate: int = 0) -> SynthesisResult:
        try:
            from piper import SynthesisConfig
        except ImportError as exc:
            raise EngineUnavailable("piper-tts não instalado") from exc
        model_path = self._model_path(voice)
        model, lock = self._load(model_path)
        config = SynthesisConfig(length_scale=1 / max(0.3, 1 + rate / 100))
        try:
            with lock, wave.open(str(out_path), "wb") as wav_file:
                model.synthesize_wav(text, wav_file, syn_config=config)
        except Exception as exc:
            raise EngineError(f"Falha no Piper: {exc}") from exc
        if not out_path.exists() or out_path.stat().st_size <= 44:
            raise EngineError("Piper não gerou áudio")
        return SynthesisResult(out_path)
