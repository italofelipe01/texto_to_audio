"""FFmpeg toolbox: every audio/video post-production step of the pipeline.

Steps (all optional except chunk preparation and concatenation):

* chunk preparation: high quality resampling (soxr), tempo/pitch with
  Rubber Band (formants preserved), silence trimming and pauses;
* lossless concatenation (concat demuxer);
* voice enhancement: high-pass, presence EQ, de-esser and compressor;
* background music with automatic ducking (sidechaincompress), fades,
  intro/outro jingles;
* two-pass EBU R128 loudness normalization (loudnorm);
* multi-format encoding with ID3/MP4 tags, chapters and cover art;
* waveform image + peaks for the web player;
* MP4 video with animated background, waveform, title and burned subtitles.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import textwrap
import wave
from array import array
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48_000


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


# --------------------------------------------------------------------------- #
# Output formats
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AudioFormat:
    key: str
    label: str
    ext: str
    encoder: str
    mime: str
    bitrate_mono: str | None
    bitrate_stereo: str | None
    cover: bool
    chapters: bool
    extra: tuple[str, ...] = ()


FORMATS: dict[str, AudioFormat] = {
    f.key: f
    for f in (
        AudioFormat(
            "mp3",
            "MP3 (compatível com tudo)",
            "mp3",
            "libmp3lame",
            "audio/mpeg",
            "96k",
            "192k",
            True,
            True,
            ("-id3v2_version", "3", "-write_id3v1", "1"),
        ),
        AudioFormat(
            "m4b",
            "M4B (audiobook com capítulos)",
            "m4b",
            "aac",
            "audio/mp4",
            "80k",
            "128k",
            True,
            True,
            ("-f", "ipod", "-movflags", "+faststart"),
        ),
        AudioFormat(
            "m4a",
            "M4A / AAC",
            "m4a",
            "aac",
            "audio/mp4",
            "96k",
            "160k",
            True,
            True,
            ("-movflags", "+faststart"),
        ),
        AudioFormat(
            "opus",
            "Opus (menor arquivo, ótima qualidade)",
            "opus",
            "libopus",
            "audio/ogg",
            "48k",
            "96k",
            False,
            True,
            ("-ar", "48000"),
        ),
        AudioFormat(
            "flac",
            "FLAC (sem perdas)",
            "flac",
            "flac",
            "audio/flac",
            None,
            None,
            True,
            False,
            ("-compression_level", "8"),
        ),
        AudioFormat(
            "wav",
            "WAV (sem compressão)",
            "wav",
            "pcm_s16le",
            "audio/wav",
            None,
            None,
            False,
            False,
            (),
        ),
    )
}

VIDEO_SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920), "square": (1080, 1080)}
VIDEO_STYLES = ("waves", "bars", "none")

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


class FFmpeg:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg_bin = shutil.which(ffmpeg) or ffmpeg
        self.ffprobe_bin = shutil.which(ffprobe) or ffprobe

    def available(self) -> bool:
        return bool(shutil.which(self.ffmpeg_bin) and shutil.which(self.ffprobe_bin))

    def _query(self, flag: str) -> str:
        try:
            return subprocess.run(
                [self.ffmpeg_bin, "-hide_banner", flag],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    @cached_property
    def version(self) -> str:
        first = self._query("-version").splitlines()
        match = re.search(r"version\s+(\S+)", first[0]) if first else None
        return match.group(1) if match else "desconhecida"

    @cached_property
    def filters(self) -> set[str]:
        names = set()
        for line in self._query("-filters").splitlines():
            parts = line.split()
            if len(parts) >= 3 and "->" in parts[2]:
                names.add(parts[1])
        return names

    @cached_property
    def encoders(self) -> set[str]:
        names = set()
        for line in self._query("-encoders").splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
                names.add(parts[1])
        return names

    @cached_property
    def soxr(self) -> bool:
        return "--enable-libsoxr" in self._query("-version")

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def capabilities(self) -> dict:
        features = {
            "rubberband": "Velocidade/tom com alta qualidade (preserva formantes)",
            "loudnorm": "Normalização de loudness EBU R128 em 2 passagens",
            "sidechaincompress": "Ducking automático da música de fundo",
            "deesser": "Redução de sibilância",
            "acompressor": "Compressor dinâmico",
            "silenceremove": "Remoção de silêncios",
            "showwaves": "Forma de onda animada no vídeo",
            "showwavespic": "Imagem da forma de onda",
            "showfreqs": "Barras de espectro no vídeo",
            "subtitles": "Legendas embutidas no vídeo (libass)",
            "drawtext": "Título no vídeo",
            "gradients": "Fundo animado em gradiente",
            "aresample": "Reamostragem",
        }
        return {
            "available": self.available(),
            "version": self.version if self.available() else None,
            "filters": {name: self.has_filter(name) for name in features},
            "filter_descriptions": features,
            "encoders": {
                name: self.has_encoder(name)
                for name in (
                    "libmp3lame",
                    "aac",
                    "libopus",
                    "flac",
                    "pcm_s16le",
                    "libx264",
                    "mjpeg",
                )
            },
            "formats": {key: self.has_encoder(fmt.encoder) for key, fmt in FORMATS.items()},
            "video": self.has_encoder("libx264") and self.has_filter("showwaves"),
            "soxr": self.soxr,
        }

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        loglevel: str = "error",
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = [self.ffmpeg_bin, "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel, *args]
        logger.debug("ffmpeg: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace", cwd=cwd, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise FFmpegError(
                "FFmpeg não encontrado. Instale-o (ex.: sudo apt install ffmpeg) ou use o Docker."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError("FFmpeg excedeu o tempo limite") from exc
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
            raise FFmpegError(f"FFmpeg falhou: {tail}", proc.stderr)
        return proc

    def probe(self, path: Path) -> dict:
        try:
            proc = subprocess.run(
                [
                    self.ffprobe_bin,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    "-show_chapters",
                    str(path),
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise FFmpegError("ffprobe não encontrado") from exc
        if proc.returncode != 0:
            raise FFmpegError(f"ffprobe falhou para {path}: {proc.stderr.strip()[-300:]}")
        return json.loads(proc.stdout or "{}")

    def duration(self, path: Path) -> float:
        if Path(path).suffix.lower() == ".wav":
            try:
                return wav_duration(Path(path))
            except (wave.Error, EOFError):
                pass
        info = self.probe(path)
        try:
            return float(info["format"]["duration"])
        except (KeyError, ValueError):
            return 0.0


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


# --------------------------------------------------------------------------- #
# Filter builders (pure functions, easy to test)
# --------------------------------------------------------------------------- #


def tempo_filters(factor: float, *, rubberband: bool = False) -> list[str]:
    if abs(factor - 1.0) < 0.005:
        return []
    if rubberband:
        return [f"rubberband=tempo={factor:.4f}:pitchq=quality"]
    filters = []
    while factor > 2.0:
        filters.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        filters.append("atempo=0.5")
        factor /= 0.5
    filters.append(f"atempo={factor:.4f}")
    return filters


def pitch_filters(
    semitones: float, *, rubberband: bool = False, sample_rate: int = SAMPLE_RATE
) -> list[str]:
    if abs(semitones) < 0.05:
        return []
    ratio = 2 ** (semitones / 12)
    if rubberband:
        return [f"rubberband=pitch={ratio:.5f}:formant=preserved:pitchq=quality"]
    return [
        f"asetrate={round(sample_rate * ratio)}",
        f"aresample={sample_rate}",
        *tempo_filters(1 / ratio),
    ]


_TRIM = "silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:start_silence=0.06"


def trim_filters() -> list[str]:
    """Trim leading and trailing silence (reverse trick keeps inner pauses intact)."""
    return [_TRIM, "areverse", _TRIM, "areverse"]


def enhance_filters(ff: FFmpeg | None = None) -> list[str]:
    """Broadcast-style voice chain: rumble cut, presence, de-ess, gentle compression."""
    chain = [
        "highpass=f=70",
        "equalizer=f=250:t=q:w=1.0:g=-1.5",
        "equalizer=f=3500:t=q:w=1.2:g=2",
    ]
    if ff is None or ff.has_filter("deesser"):
        chain.append("deesser=i=0.4:m=0.5:f=0.5")
    chain.append("acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=1")
    return chain


def resample_filter(ff: FFmpeg | None, sample_rate: int = SAMPLE_RATE) -> str:
    """High quality SoX resampler when FFmpeg was built with it."""
    if ff is not None and ff.soxr:
        return f"aresample={sample_rate}:resampler=soxr:precision=28"
    return f"aresample={sample_rate}"


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def prepare_chunk(
    ff: FFmpeg,
    src: Path,
    dst: Path,
    *,
    tempo: float = 1.0,
    pitch: float = 0.0,
    trim: bool = True,
    pause: float = 0.0,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[float, float]:
    """Normalize a synthesized chunk to mono PCM and append a pause.

    Returns ``(speech_seconds, total_seconds)``.
    """
    rubberband = ff.has_filter("rubberband")
    filters = [resample_filter(ff, sample_rate), "aformat=sample_fmts=s16:channel_layouts=mono"]
    filters += pitch_filters(pitch, rubberband=rubberband, sample_rate=sample_rate)
    filters += tempo_filters(tempo, rubberband=rubberband)
    if trim and ff.has_filter("silenceremove"):
        filters += trim_filters()
    speech_path = dst.with_suffix(".speech.wav")
    ff.run(
        [
            "-i",
            str(src),
            "-af",
            ",".join(filters),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(speech_path),
        ]
    )
    speech = wav_duration(speech_path)
    if pause > 0:
        ff.run(
            [
                "-i",
                str(speech_path),
                "-af",
                f"apad=pad_dur={pause:.3f}",
                "-c:a",
                "pcm_s16le",
                str(dst),
            ]
        )
        speech_path.unlink(missing_ok=True)
    else:
        speech_path.replace(dst)
    return speech, wav_duration(dst)


def concat_wavs(ff: FFmpeg, files: list[Path], dst: Path) -> None:
    """Lossless concatenation of identically formatted WAV files."""
    list_file = dst.with_suffix(".txt")
    lines = []
    for path in files:
        escaped = str(Path(path).resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ff.run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(dst)])
    list_file.unlink(missing_ok=True)


@dataclass
class MixResult:
    path: Path
    offset: float  # seconds of audio placed before the voice (intro + music lead-in)
    duration: float
    channels: int


def mix(
    ff: FFmpeg,
    voice: Path,
    dst: Path,
    *,
    enhance: bool = True,
    music: Path | None = None,
    music_gain_db: float = -20.0,
    ducking: bool = True,
    music_lead: float = 2.0,
    music_tail: float = 3.0,
    intro: Path | None = None,
    outro: Path | None = None,
    sample_rate: int = SAMPLE_RATE,
) -> MixResult:
    """Voice enhancement + background music with ducking + intro/outro, in one filtergraph."""
    voice_duration = ff.duration(voice)
    stereo = bool(music or intro or outro)
    layout = "stereo" if stereo else "mono"
    fmt = f"{resample_filter(ff, sample_rate)},aformat=sample_fmts=fltp:channel_layouts={layout}"

    inputs = ["-i", str(voice)]
    graph: list[str] = []
    voice_chain = [fmt] + (enhance_filters(ff) if enhance else [])
    offset = 0.0

    if music:
        lead, tail = max(0.0, music_lead), max(0.5, music_tail)
        total = lead + voice_duration + tail
        delay_ms = int(lead * 1000)
        voice_chain += [f"adelay=delays={delay_ms}:all=1", f"apad=pad_dur={tail:.3f}"]
        graph.append(f"[0:a]{','.join(voice_chain)},asplit=2[voice][sc]")
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        graph.append(
            f"[1:a]{fmt},volume={music_gain_db:.1f}dB,afade=t=in:d={min(2.0, max(lead, 0.5)):.2f}[music]"
        )
        if ducking and ff.has_filter("sidechaincompress"):
            graph.append(
                "[music][sc]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=700:makeup=1[bed]"
            )
        else:
            graph.append("[sc]anullsink;[music]anull[bed]")
        graph.append(
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"afade=t=out:st={max(0.0, total - tail):.3f}:d={tail:.3f}[main]"
        )
        offset += lead
    else:
        graph.append(f"[0:a]{','.join(voice_chain)}[main]")

    parts = ["[main]"]
    if intro:
        idx = inputs.count("-i")
        inputs += ["-i", str(intro)]
        graph.append(f"[{idx}:a]{fmt}[intro]")
        parts.insert(0, "[intro]")
        offset += ff.duration(intro)
    if outro:
        idx = inputs.count("-i")
        inputs += ["-i", str(outro)]
        graph.append(f"[{idx}:a]{fmt}[outro]")
        parts.append("[outro]")
    if len(parts) > 1:
        graph.append(f"{''.join(parts)}concat=n={len(parts)}:v=0:a=1[out]")
    else:
        graph[-1] = graph[-1].rsplit("[main]", 1)[0] + "[out]"

    ff.run(
        [
            *inputs,
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[out]",
            "-c:a",
            "pcm_f32le",
            "-ar",
            str(sample_rate),
            str(dst),
        ]
    )
    return MixResult(dst, offset, ff.duration(dst), 2 if stereo else 1)


def _parse_loudnorm_json(stderr: str) -> dict | None:
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None


def measure_loudness(
    ff: FFmpeg, src: Path, *, target: float = -16.0, true_peak: float = -1.5
) -> dict | None:
    """First loudnorm pass: integrated loudness, true peak, LRA and threshold."""
    proc = ff.run(
        [
            "-i",
            str(src),
            "-af",
            f"loudnorm=I={target}:TP={true_peak}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        loglevel="info",
    )
    data = _parse_loudnorm_json(proc.stderr)
    if not data:
        return None
    try:
        if float(data["input_i"]) == float("-inf"):
            return None
    except (KeyError, ValueError):
        return None
    return data


def normalize_loudness(
    ff: FFmpeg,
    src: Path,
    dst: Path,
    *,
    target: float | None = -16.0,
    true_peak: float = -1.5,
    sample_rate: int = SAMPLE_RATE,
) -> dict | None:
    """Two-pass EBU R128 normalization (linear when possible). Returns pass-1 stats."""
    if target is None:
        ff.run(
            [
                "-i",
                str(src),
                "-af",
                f"alimiter=limit={10 ** (true_peak / 20):.4f}",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(sample_rate),
                str(dst),
            ]
        )
        return None
    measured = measure_loudness(ff, src, target=target, true_peak=true_peak)
    if measured:
        loudnorm = (
            f"loudnorm=I={target}:TP={true_peak}:LRA=11"
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true"
        )
    else:
        loudnorm = f"loudnorm=I={target}:TP={true_peak}:LRA=11"
    ff.run(
        [
            "-i",
            str(src),
            "-af",
            f"{loudnorm},aresample={sample_rate}",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            str(dst),
        ]
    )
    return measured


def write_ffmetadata(
    path: Path, tags: dict[str, str], chapters: list[tuple[float, float, str]]
) -> None:
    def esc(value: str) -> str:
        return re.sub(r"([=;#\\\n])", r"\\\1", str(value))

    lines = [";FFMETADATA1"]
    lines += [f"{key}={esc(val)}" for key, val in tags.items() if val]
    for start, end, title in chapters:
        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(start * 1000))}",
            f"END={int(round(end * 1000))}",
            f"title={esc(title)}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_cover(ff: FFmpeg, src: Path, dst: Path, size: int = 1400) -> Path:
    """Square JPEG cover (podcast/audiobook stores require square art)."""
    ff.run(
        [
            "-i",
            str(src),
            "-vf",
            f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dst),
        ]
    )
    return dst


def encode(
    ff: FFmpeg,
    src: Path,
    dst: Path,
    fmt: AudioFormat,
    *,
    channels: int = 1,
    bitrate: str | None = None,
    metadata_file: Path | None = None,
    cover: Path | None = None,
    sample_rate: int = SAMPLE_RATE,
    speech_only: bool = True,
) -> Path:
    args = ["-i", str(src)]
    if metadata_file:
        args += ["-i", str(metadata_file)]
    use_cover = bool(cover and fmt.cover)
    if use_cover:
        args += ["-i", str(cover)]
    args += ["-map", "0:a"]
    if use_cover:
        args += [
            "-map",
            f"{2 if metadata_file else 1}:v",
            "-c:v",
            "copy",
            "-disposition:v:0",
            "attached_pic",
        ]
    if metadata_file:
        args += ["-map_metadata", "1", "-map_chapters", "1" if fmt.chapters else "-1"]
    args += ["-c:a", fmt.encoder]
    rate = bitrate or (fmt.bitrate_stereo if channels > 1 else fmt.bitrate_mono)
    if rate:
        args += ["-b:a", rate]
    if fmt.encoder == "libopus":
        args += ["-application", "voip" if speech_only else "audio"]
    else:
        args += ["-ar", str(sample_rate)]
    args += list(fmt.extra)
    args.append(str(dst))
    ff.run(args)
    return dst


def waveform_png(
    ff: FFmpeg,
    src: Path,
    dst: Path,
    *,
    width: int = 1600,
    height: int = 240,
    color: str = "0x3b82f6",
) -> Path:
    ff.run(
        [
            "-i",
            str(src),
            "-filter_complex",
            f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors={color}:scale=sqrt:filter=peak",
            "-frames:v",
            "1",
            str(dst),
        ]
    )
    return dst


def waveform_peaks(
    ff: FFmpeg, src: Path, buckets: int = 1000, sample_rate: int = 2000
) -> list[float]:
    """Normalized peak per bucket, for drawing an interactive waveform in the browser."""
    proc = subprocess.run(
        [
            ff.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise FFmpegError("Falha ao calcular a forma de onda", proc.stderr.decode(errors="replace"))
    samples = array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if not samples:
        return []
    size = max(1, len(samples) // buckets)
    peaks = []
    for i in range(0, len(samples), size):
        window = samples[i : i + size]
        peaks.append(max(max(window), -min(window)))
    top = max(peaks) or 1
    return [round(p / top, 3) for p in peaks[:buckets]]


def _find_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def render_video(
    ff: FFmpeg,
    audio: Path,
    dst: Path,
    *,
    workdir: Path,
    orientation: str = "landscape",
    style: str = "waves",
    title: str | None = None,
    subtitles: Path | None = None,
    background: Path | None = None,
    accent: str = "0x38bdf8",
    fps: int = 25,
) -> Path:
    """MP4 (H.264/AAC) for YouTube, Reels, Shorts or WhatsApp status."""
    width, height = VIDEO_SIZES.get(orientation, VIDEO_SIZES["landscape"])
    portrait = height > width
    # FFmpeg runs inside workdir (so filter arguments need no path escaping):
    # every input must therefore be absolute.
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    audio = Path(audio).resolve()
    background = Path(background).resolve() if background else None
    inputs = ["-i", str(audio)]
    graph: list[str] = []

    # The background is rendered once to a still image and looped: animating or
    # blurring it on every frame would make rendering several times slower.
    bg_png = workdir / "fundo.png"
    if background:
        ff.run(
            [
                "-i",
                str(background),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                "boxblur=12:2,eq=brightness=-0.18:saturation=0.85",
                "-frames:v",
                "1",
                str(bg_png),
            ]
        )
    elif ff.has_filter("gradients"):
        ff.run(
            [
                "-f",
                "lavfi",
                "-i",
                f"gradients=s={width}x{height}:c0=0x0f172a:c1=0x1e3a8a:x0=0:y0=0:"
                f"x1={width}:y1={height}:nb_colors=2",
                "-frames:v",
                "1",
                str(bg_png),
            ]
        )
    else:
        ff.run(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x0f172a:s={width}x{height}",
                "-frames:v",
                "1",
                str(bg_png),
            ]
        )
    inputs += ["-i", str(bg_png)]
    graph.append(
        f"[1:v]format=yuv420p,loop=loop=-1:size=1:start=0,settb=1/{fps},setpts=N/{fps}/TB[bg]"
    )

    current = "[bg]"
    wave_w = int(width * (0.88 if portrait else 0.8))
    wave_h = int(height * (0.16 if portrait else 0.26))
    wave_y = f"(H-h)/2-{int(height * 0.06)}" if portrait else "(H-h)/2"
    if style == "waves" and ff.has_filter("showwaves"):
        graph.append(
            f"[0:a]aformat=channel_layouts=mono,showwaves=s={wave_w}x{wave_h}:mode=cline:rate={fps}:"
            f"colors={accent}:scale=sqrt:draw=full,format=rgba[viz]"
        )
    elif style == "bars" and ff.has_filter("showfreqs"):
        graph.append(
            f"[0:a]aformat=channel_layouts=mono,showfreqs=s={wave_w}x{wave_h}:mode=bar:ascale=sqrt:"
            f"fscale=log:win_size=2048:rate={fps}:colors={accent},format=rgba[viz]"
        )
    else:
        style = "none"
    if style != "none":
        graph.append(f"{current}[viz]overlay=(W-w)/2:{wave_y}:shortest=1:format=yuv420[v_viz]")
        current = "[v_viz]"

    if title and ff.has_filter("drawtext"):
        wrap = 22 if portrait else (28 if width == height else 40)
        lines = textwrap.wrap(title, wrap, max_lines=4, placeholder=" …")
        font = _find_font()
        font_opt = f"fontfile='{font}'" if font else "font=Sans"
        size = int(height * (0.038 if portrait else 0.06))
        top = int(height * (0.10 if portrait else 0.08))
        # one drawtext per line so every line is centered (works on any FFmpeg version)
        for i, line in enumerate(lines):
            (workdir / f"titulo{i}.txt").write_text(line, encoding="utf-8")
            graph.append(
                f"{current}drawtext=textfile=titulo{i}.txt:{font_opt}:fontcolor=white:fontsize={size}:"
                f"x=(w-text_w)/2:y={top + int(i * size * 1.3)}:shadowcolor=black@0.5:shadowx=2:shadowy=2"
                f"[v_title{i}]"
            )
            current = f"[v_title{i}]"

    if subtitles and subtitles.exists() and ff.has_filter("subtitles"):
        shutil.copyfile(subtitles, workdir / "subs.srt")
        font_size, margin = (11, 70) if portrait else ((13, 30) if width == height else (16, 24))
        style_opts = (
            f"FontName=DejaVu Sans,FontSize={font_size},PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H90000000,BackColour=&H80000000,BorderStyle=1,Outline=2,Shadow=0,"
            f"Alignment=2,MarginV={margin},MarginL=20,MarginR=20"
        )
        graph.append(f"{current}subtitles=subs.srt:force_style='{style_opts}'[v_subs]")
        current = "[v_subs]"

    graph.append(f"{current}format=yuv420p[vout]")
    ff.run(
        [
            *inputs,
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[vout]",
            "-map",
            "0:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-r",
            str(fps),
            "-g",
            str(fps * 2),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            str(Path(dst).resolve()),
        ],
        cwd=workdir,
    )
    return dst
