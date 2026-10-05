"""eSpeak NG: robotic but always-available offline fallback (``apt install espeak-ng``)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import EngineError, EngineUnavailable, SynthesisResult, TTSEngine, Voice, locale_matches

VOICES = {
    "pt-br": ("pt-BR", "Português (Brasil)"),
    "pt": ("pt-PT", "Português (Portugal)"),
    "en-us": ("en-US", "Inglês (EUA)"),
    "es": ("es-ES", "Espanhol"),
    "fr-fr": ("fr-FR", "Francês"),
}

BASE_WPM = 165


class EspeakEngine(TTSEngine):
    name = "espeak"
    label = "eSpeak NG (offline)"
    description = "Sintetizador local leve e sempre disponível; qualidade robótica, usado como último recurso."
    online = False
    native_rate = True
    output_ext = "wav"

    def _binary(self) -> str | None:
        return shutil.which("espeak-ng") or shutil.which("espeak")

    def check(self) -> tuple[bool, str]:
        if self._binary():
            return True, "ok (offline)"
        return False, "espeak-ng não encontrado (apt install espeak-ng)"

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        return [
            Voice(id=vid, name=label, locale=loc, engine=self.name, quality="robotic")
            for vid, (loc, label) in VOICES.items()
            if locale_matches(loc, locale)
        ]

    def default_voice(self, locale: str) -> str | None:
        for vid, (loc, _label) in VOICES.items():
            if locale_matches(loc, locale):
                return vid
        return None

    def synthesize(self, text: str, voice: str, out_path: Path, rate: int = 0) -> SynthesisResult:
        binary = self._binary()
        if not binary:
            raise EngineUnavailable("espeak-ng não instalado")
        wpm = int(max(80, min(450, BASE_WPM * (1 + rate / 100))))
        try:
            subprocess.run(
                [binary, "-v", voice, "-s", str(wpm), "-w", str(out_path), "--stdin"],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
                timeout=300,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            raise EngineError(f"Falha no eSpeak: {stderr.decode(errors='replace')[-300:]}") from exc
        if not out_path.exists() or out_path.stat().st_size <= 44:
            raise EngineError("eSpeak não gerou áudio")
        return SynthesisResult(out_path)
