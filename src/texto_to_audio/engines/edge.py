"""Microsoft Edge neural voices via ``edge-tts`` (free, no API key, needs internet)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from .base import EngineError, EngineUnavailable, SynthesisResult, TTSEngine, Voice, locale_matches

logger = logging.getLogger(__name__)

# Used when the voice list cannot be downloaded (offline start, proxy issues...).
FALLBACK_VOICES = [
    ("pt-BR-FranciscaNeural", "Female"),
    ("pt-BR-AntonioNeural", "Male"),
    ("pt-BR-ThalitaMultilingualNeural", "Female"),
    ("pt-PT-RaquelNeural", "Female"),
    ("pt-PT-DuarteNeural", "Male"),
    ("en-US-AvaMultilingualNeural", "Female"),
    ("en-US-AndrewMultilingualNeural", "Male"),
    ("es-ES-ElviraNeural", "Female"),
    ("es-MX-JorgeNeural", "Male"),
    ("fr-FR-DeniseNeural", "Female"),
]

DEFAULT_VOICES = {
    "pt-br": "pt-BR-FranciscaNeural",
    "pt-pt": "pt-PT-RaquelNeural",
    "pt": "pt-BR-FranciscaNeural",
    "en": "en-US-AvaMultilingualNeural",
    "en-us": "en-US-AvaMultilingualNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
}

_VOICE_CACHE_TTL = 7 * 24 * 3600


def _friendly_name(short_name: str) -> str:
    name = short_name.split("-", 2)[-1]
    return name.replace("MultilingualNeural", " (multilíngue)").replace("Neural", "")


class EdgeEngine(TTSEngine):
    name = "edge"
    label = "Microsoft Edge Neural"
    description = (
        "Vozes neurais de altíssima qualidade, gratuitas, sem chave de API (requer internet)."
    )
    online = True
    native_rate = True
    output_ext = "mp3"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._voices: list[Voice] | None = None
        self._retry_at = 0.0  # when the offline fallback list should be refreshed

    def check(self) -> tuple[bool, str]:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False, "pacote 'edge-tts' não instalado (pip install edge-tts)"
        return True, "ok (requer internet)"

    def signature(self) -> str:
        try:
            from edge_tts import version

            return f"edge-{version.__version__}"
        except Exception:  # pragma: no cover - defensive
            return "edge"

    def _apply_ca_bundle(self) -> None:
        """Trust an extra CA bundle (corporate proxies with TLS inspection).

        edge-tts pins certifi's bundle, ignoring SSL_CERT_FILE, so the module
        level SSL contexts are extended when ``TTA_CA_BUNDLE`` is set.
        """
        bundle = self.settings.ca_bundle
        if not bundle or getattr(self, "_ca_applied", False):
            return
        import ssl

        import certifi
        from edge_tts import communicate, voices

        context = ssl.create_default_context(cafile=certifi.where())
        context.load_verify_locations(cafile=str(bundle))
        communicate._SSL_CTX = context
        voices._SSL_CTX = context
        self._ca_applied = True

    # -- voices ------------------------------------------------------------ #

    def _cache_file(self) -> Path:
        return Path(self.settings.cache_dir) / "edge_voices.json"

    def _load_voices(self) -> list[Voice]:
        if self._voices is not None and time.time() >= self._retry_at > 0:
            self._voices = None
            self._retry_at = 0.0
        if self._voices is not None:
            return self._voices
        cache_file = self._cache_file()
        raw: list[dict] | None = None
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < _VOICE_CACHE_TTL:
            try:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
        if raw is None:
            try:
                import edge_tts

                self._apply_ca_bundle()
                raw = asyncio.run(edge_tts.list_voices(proxy=self.settings.proxy))
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                logger.warning("Não foi possível baixar a lista de vozes do Edge: %s", exc)
                self._retry_at = time.time() + 600
                raw = [
                    {"ShortName": short, "Gender": gender, "Locale": short[:5]}
                    for short, gender in FALLBACK_VOICES
                ]
        self._voices = [
            Voice(
                id=item["ShortName"],
                name=_friendly_name(item["ShortName"]),
                locale=item.get("Locale", item["ShortName"][:5]),
                engine=self.name,
                gender=item.get("Gender"),
                quality="neural",
            )
            for item in raw
            if "ShortName" in item
        ]
        return self._voices

    def list_voices(self, locale: str | None = None) -> list[Voice]:
        return [v for v in self._load_voices() if locale_matches(v.locale, locale)]

    def default_voice(self, locale: str) -> str | None:
        key = locale.lower().replace("_", "-")
        return DEFAULT_VOICES.get(key) or DEFAULT_VOICES.get(key.split("-")[0])

    # -- synthesis ----------------------------------------------------------- #

    def synthesize(self, text: str, voice: str, out_path: Path, rate: int = 0) -> SynthesisResult:
        try:
            import edge_tts
        except ImportError as exc:
            raise EngineUnavailable("edge-tts não instalado") from exc

        self._apply_ca_bundle()
        sentences: list[tuple[float, float, str]] = []
        try:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=f"{int(rate):+d}%",
                boundary="SentenceBoundary",
                proxy=self.settings.proxy,
            )
        except (TypeError, ValueError) as exc:
            raise EngineError(f"Parâmetros inválidos para o Edge TTS: {exc}") from exc

        async def _run() -> None:
            with open(out_path, "wb") as fh:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                        start = chunk["offset"] / 10_000_000
                        end = (chunk["offset"] + chunk["duration"]) / 10_000_000
                        sentences.append((start, end, chunk["text"]))

        try:
            asyncio.run(_run())
        except Exception as exc:
            raise EngineError(f"Falha no Edge TTS: {exc}") from exc
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise EngineError("Edge TTS não retornou áudio")
        return SynthesisResult(out_path, sentences)
