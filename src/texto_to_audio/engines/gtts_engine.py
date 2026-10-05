"""Google Translate voices via ``gTTS`` (free, needs internet).

gTTS has no speed control, so the pipeline applies tempo changes with FFmpeg.
"""

from __future__ import annotations

from pathlib import Path

from .base import EngineError, EngineUnavailable, SynthesisResult, TTSEngine, Voice, locale_matches

# voice id -> (locale, gTTS lang, top level domain for the accent, display name)
ACCENTS = {
    "pt-BR": ("pt-BR", "pt", "com.br", "Português (Brasil)"),
    "pt-PT": ("pt-PT", "pt", "pt", "Português (Portugal)"),
    "en-US": ("en-US", "en", "us", "Inglês (EUA)"),
    "en-GB": ("en-GB", "en", "co.uk", "Inglês (Reino Unido)"),
    "es-ES": ("es-ES", "es", "es", "Espanhol (Espanha)"),
    "es-MX": ("es-MX", "es", "com.mx", "Espanhol (México)"),
    "fr-FR": ("fr-FR", "fr", "fr", "Francês (França)"),
    "it-IT": ("it-IT", "it", "it", "Italiano"),
    "de-DE": ("de-DE", "de", "de", "Alemão"),
}


class GTTSEngine(TTSEngine):
    name = "gtts"
    label = "Google Translate (gTTS)"
    description = "Voz do Google Tradutor, gratuita e estável (requer internet; sem controle nativo de velocidade)."
    online = True
    native_rate = False
    output_ext = "mp3"
    max_concurrency = 2

    def check(self) -> tuple[bool, str]:
        try:
            import gtts  # noqa: F401
        except ImportError:
            return False, "pacote 'gTTS' não instalado (pip install gTTS)"
        return True, "ok (requer internet)"

    def signature(self) -> str:
        try:
            from gtts import version

            return f"gtts-{version.__version__}"
        except Exception:  # pragma: no cover
            return "gtts"

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        return [
            Voice(id=vid, name=label, locale=loc, engine=self.name, quality="standard")
            for vid, (loc, _lang, _tld, label) in ACCENTS.items()
            if locale_matches(loc, locale)
        ]

    def default_voice(self, locale: str) -> str | None:
        for vid, (loc, *_rest) in ACCENTS.items():
            if locale_matches(loc, locale):
                return vid
        return None

    def synthesize(self, text: str, voice: str, out_path: Path, rate: int = 0) -> SynthesisResult:
        try:
            from gtts import gTTS
        except ImportError as exc:
            raise EngineUnavailable("gTTS não instalado") from exc
        if voice not in ACCENTS:
            raise EngineError(f"Voz gTTS desconhecida: {voice}")
        _loc, lang, tld, _label = ACCENTS[voice]
        try:
            gTTS(text, lang=lang, tld=tld).save(str(out_path))
        except Exception as exc:
            raise EngineError(f"Falha no gTTS: {exc}") from exc
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise EngineError("gTTS não retornou áudio")
        return SynthesisResult(out_path)
