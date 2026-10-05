"""Job options and ready-made presets (audiobook, podcast, vídeo, ...)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .ffmpeg import FORMATS, VIDEO_SIZES, VIDEO_STYLES


@dataclass
class JobOptions:
    # Voz
    engine: str = "auto"
    voice: str | None = None
    lang: str = "pt-BR"
    rate: int = 0  # velocidade em % (-50 a +100)
    pitch: float = 0.0  # tom em semitons (-12 a +12)
    fallback: bool = True
    # Texto
    max_chunk_chars: int = 400
    speak_titles: bool = True
    strip_emojis: bool = True
    lexicon: dict[str, str] = field(default_factory=dict)
    # Pausas (segundos)
    pause_sentence: float = 0.2
    pause_paragraph: float = 0.6
    pause_section: float = 1.4
    # Pós-produção
    trim_silence: bool = True
    enhance: bool = True
    loudness: float | None = -16.0  # LUFS; None desativa a normalização
    true_peak: float = -1.5
    sample_rate: int = 48_000
    # Trilha e vinhetas
    music: str | None = None
    music_gain_db: float = -20.0
    ducking: bool = True
    intro: str | None = None
    outro: str | None = None
    # Saídas
    formats: list[str] = field(default_factory=lambda: ["mp3"])
    bitrate: str | None = None
    subtitles: bool = True
    chapters: bool = True
    waveform: bool = True
    video: str | None = None  # landscape | portrait | square
    video_style: str = "waves"
    burn_subtitles: bool = True
    cover: str | None = None
    background: str | None = None
    # Metadados
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    basename: str | None = None
    preset: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> JobOptions:
        """Build options from untrusted input (CLI, web form, sidecar JSON).

        ``None`` means "not specified" and never overrides the preset; use
        ``"off"`` to disable loudness normalization explicitly.
        """
        data = dict(data or {})
        preset = data.get("preset")
        base = PRESETS[preset].options if preset in PRESETS else {}
        merged = {**base, **{k: v for k, v in data.items() if v is not None}}
        known = {f.name for f in fields(cls)}
        options = cls(**{k: v for k, v in merged.items() if k in known})
        options.preset = preset if preset in PRESETS else None
        return options.validated()

    def validated(self) -> JobOptions:
        def clamp(value, lo, hi, cast=float):
            try:
                return max(lo, min(hi, cast(value)))
            except (TypeError, ValueError):
                return cast(0 if lo <= 0 <= hi else lo)

        self.rate = clamp(self.rate, -50, 100, lambda v: int(round(float(v))))
        self.pitch = clamp(self.pitch, -12, 12)
        self.max_chunk_chars = clamp(self.max_chunk_chars, 80, 3000, int)
        for name in ("pause_sentence", "pause_paragraph", "pause_section"):
            setattr(self, name, clamp(getattr(self, name), 0, 10))
        if self.loudness in ("", "off", "none", False):
            self.loudness = None
        if self.loudness is not None:
            self.loudness = clamp(self.loudness, -36, -6)
        self.true_peak = clamp(self.true_peak, -9, 0)
        self.music_gain_db = clamp(self.music_gain_db, -40, 0)
        self.sample_rate = 44_100 if int(self.sample_rate or 0) == 44_100 else 48_000
        if isinstance(self.formats, str):
            self.formats = [f.strip() for f in self.formats.split(",")]
        self.formats = [f for f in dict.fromkeys(f.lower() for f in self.formats) if f in FORMATS]
        if not self.formats:
            self.formats = ["mp3"]
        if self.video not in VIDEO_SIZES:
            self.video = None
        if self.video_style not in VIDEO_STYLES:
            self.video_style = "waves"
        if not isinstance(self.lexicon, dict):
            self.lexicon = {}
        self.engine = (self.engine or "auto").lower()
        self.voice = self.voice or None
        return self


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    description: str
    options: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        Preset(
            "padrao",
            "Padrão",
            "MP3 com voz tratada, loudness de -16 LUFS, legendas e capítulos.",
            {},
        ),
        Preset(
            "audiobook",
            "Audiobook",
            "M4B com capítulos + MP3, ritmo calmo, -18 LUFS e pico de -3 dBTP (padrão ACX), 44,1 kHz.",
            {
                "formats": ["m4b", "mp3"],
                "loudness": -18.0,
                "true_peak": -3.0,
                "rate": -5,
                "pause_paragraph": 0.9,
                "pause_section": 2.0,
                "sample_rate": 44_100,
                "genre": "Audiobook",
            },
        ),
        Preset(
            "podcast",
            "Podcast",
            "MP3 a -16 LUFS, voz com EQ/compressor/de-esser e trilha com ducking automático.",
            {
                "formats": ["mp3"],
                "loudness": -16.0,
                "true_peak": -1.0,
                "enhance": True,
                "music_gain_db": -18.0,
                "genre": "Podcast",
            },
        ),
        Preset(
            "acessibilidade",
            "Acessibilidade",
            "Fala mais pausada, MP3 + Opus leve e transcrição sincronizada para leitura acompanhada.",
            {
                "rate": -10,
                "pause_sentence": 0.35,
                "pause_paragraph": 0.9,
                "formats": ["mp3", "opus"],
                "loudness": -16.0,
            },
        ),
        Preset(
            "video",
            "Vídeo (YouTube)",
            "MP4 1920×1080 com forma de onda, título e legendas embutidas, -14 LUFS.",
            {"video": "landscape", "loudness": -14.0, "true_peak": -1.0, "formats": ["mp3"]},
        ),
        Preset(
            "shorts",
            "Vídeo vertical",
            "MP4 1080×1920 para Reels, Shorts, TikTok e status, com legendas grandes, -14 LUFS.",
            {
                "video": "portrait",
                "loudness": -14.0,
                "true_peak": -1.0,
                "rate": 5,
                "formats": ["mp3"],
            },
        ),
        Preset(
            "rascunho",
            "Rascunho rápido",
            "Só MP3, sem tratamento extra nem legendas: ideal para revisar o texto rapidamente.",
            {
                "enhance": False,
                "loudness": None,
                "subtitles": False,
                "chapters": False,
                "waveform": False,
                "formats": ["mp3"],
            },
        ),
    )
}
