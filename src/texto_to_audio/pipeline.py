"""End-to-end orchestration: text → voice → FFmpeg post-production → deliverables."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from . import ffmpeg as fx
from .cache import SynthCache
from .config import Settings
from .engines import (
    EngineError,
    EngineUnavailable,
    SynthesisResult,
    TTSEngine,
    engine_chain,
    find_voice_engine,
)
from .presets import JobOptions
from .subtitles import ChunkTiming, anchors_from_boundaries, build_cues, to_srt, to_vtt, transcript
from .text import (
    Document,
    Segment,
    apply_lexicon,
    load_document,
    parse_lexicon,
    parse_text,
    segment_document,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]

RETRIES = 3


class PipelineError(RuntimeError):
    """User-facing error (message in Portuguese)."""


class JobCancelled(PipelineError):
    pass


@dataclass
class JobResult:
    output_dir: Path
    files: dict[str, str]
    report: dict

    def path(self, kind: str) -> Path | None:
        name = self.files.get(kind)
        return self.output_dir / name if name else None


def slugify(value: str, default: str = "audio", max_len: int = 60) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:max_len].strip("-") or default


class Pipeline:
    def __init__(self, settings: Settings | None = None, ff: fx.FFmpeg | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.ff = ff or fx.FFmpeg(self.settings.ffmpeg_bin, self.settings.ffprobe_bin)
        self.cache = SynthCache(Path(self.settings.cache_dir), self.settings.cache_max_mb)

    # ------------------------------------------------------------------ #

    def global_lexicon(self) -> dict[str, str]:
        path = self.settings.lexicon_file or (Path(self.settings.data_dir) / "pronuncia.txt")
        if path and Path(path).exists():
            try:
                return parse_lexicon(Path(path).read_text(encoding="utf-8"))
            except OSError:
                logger.warning("Não foi possível ler o dicionário de pronúncia %s", path)
        return {}

    def load(self, *, text: str | None, source: Path | None) -> Document:
        if source is not None:
            return load_document(source)
        if text is None or not text.strip():
            raise PipelineError("Nenhum texto informado.")
        return parse_text(text)

    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        options: JobOptions,
        output_dir: Path,
        text: str | None = None,
        source: Path | None = None,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> JobResult:
        started = time.monotonic()
        timings: dict[str, float] = {}
        mark = [started]

        def step(fraction: float, message: str) -> None:
            if cancel is not None and cancel.is_set():
                raise JobCancelled("Processamento cancelado.")
            if progress:
                progress(min(1.0, max(0.0, fraction)), message)
            logger.info("[%3d%%] %s", int(fraction * 100), message)

        def lap(name: str) -> None:
            now = time.monotonic()
            timings[name] = round(now - mark[0], 2)
            mark[0] = now

        if not self.ff.available():
            raise PipelineError(
                "FFmpeg/ffprobe não encontrados. Instale o FFmpeg (sudo apt install ffmpeg, "
                "brew install ffmpeg ou winget install Gyan.FFmpeg) ou use o Docker."
            )

        step(0.01, "Lendo o texto")
        try:
            doc = self.load(text=text, source=source)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        char_count = len(doc.text)
        if char_count > self.settings.max_chars:
            raise PipelineError(
                f"Texto muito longo ({char_count} caracteres). Limite: {self.settings.max_chars} "
                "(ajuste TTA_MAX_CHARS)."
            )
        segments = segment_document(doc, options.max_chunk_chars, speak_titles=options.speak_titles)
        if not segments:
            raise PipelineError("O texto está vazio após a limpeza.")
        title = options.title or doc.title or "Áudio"
        basename = slugify(options.basename or title)
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        work = output_dir / ".trabalho"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        lap("leitura")

        try:
            lexicon = {**self.global_lexicon(), **(options.lexicon or {})}
            engine, voice, synth, fallbacks, hits = self._synthesize(
                segments, options, lexicon, work, step
            )
            lap("sintese")

            # -- per-chunk processing ------------------------------------- #
            tempo = 1.0 if engine.native_rate else 1 + options.rate / 100
            pauses = {
                "sentence": options.pause_sentence,
                "paragraph": options.pause_paragraph,
                "section": options.pause_section,
                "end": 0.0,
            }
            prepared: list[tuple[float, float] | None] = [None] * len(segments)
            done = 0
            with ThreadPoolExecutor(max_workers=max(1, min(8, os.cpu_count() or 2))) as pool:
                futures = {
                    pool.submit(
                        fx.prepare_chunk,
                        self.ff,
                        synth[seg.index].path,
                        work / f"chunk_{seg.index:05d}.wav",
                        tempo=tempo,
                        pitch=options.pitch,
                        trim=options.trim_silence,
                        pause=pauses[seg.pause],
                        sample_rate=options.sample_rate,
                    ): seg.index
                    for seg in segments
                }
                for future in as_completed(futures):
                    prepared[futures[future]] = future.result()
                    done += 1
                    step(
                        0.60 + 0.10 * done / len(segments),
                        f"Tratando trechos: {done}/{len(segments)}",
                    )
            lap("trechos")

            chunk_timings: list[ChunkTiming] = []
            position = 0.0
            for seg in segments:
                speech, total = prepared[seg.index]
                chunk_timings.append(
                    ChunkTiming(
                        start=position,
                        speech=speech,
                        sentences=seg.sentences,
                        anchors=anchors_from_boundaries(
                            synth[seg.index].sentences, len(seg.sentences)
                        ),
                        section_title=seg.section_title,
                        is_title=seg.is_title,
                        extra={"section": seg.section},
                    )
                )
                position += total

            step(0.71, "Unindo trechos")
            voice_wav = work / "voz.wav"
            fx.concat_wavs(
                self.ff, [work / f"chunk_{s.index:05d}.wav" for s in segments], voice_wav
            )

            step(0.74, "Mixando voz, trilha e vinhetas")
            mixed = fx.mix(
                self.ff,
                voice_wav,
                work / "premaster.wav",
                enhance=options.enhance,
                music=Path(options.music) if options.music else None,
                music_gain_db=options.music_gain_db,
                ducking=options.ducking,
                intro=Path(options.intro) if options.intro else None,
                outro=Path(options.outro) if options.outro else None,
                sample_rate=options.sample_rate,
            )
            for timing in chunk_timings:
                timing.start += mixed.offset
            lap("mixagem")

            step(0.80, "Normalizando loudness (EBU R128, 2 passagens)")
            master = work / "master.wav"
            fx.normalize_loudness(
                self.ff,
                mixed.path,
                master,
                target=options.loudness,
                true_peak=options.true_peak,
                sample_rate=options.sample_rate,
            )
            duration = self.ff.duration(master)
            lap("loudness")

            files: dict[str, str] = {}

            # -- metadata, chapters, cover ----------------------------------- #
            chapters = self._chapters(doc, chunk_timings, duration) if options.chapters else []
            tags = {
                "title": title,
                "artist": options.artist or "Texto para Áudio",
                "album_artist": options.artist or "Texto para Áudio",
                "album": options.album or title,
                "genre": options.genre or "Speech",
                "date": str(datetime.now().year),
                "language": options.lang,
                "comment": f"Voz: {voice} ({engine.label}). Gerado por Texto para Áudio {__version__}.",
            }
            metadata_file = work / "metadata.txt"
            fx.write_ffmetadata(metadata_file, tags, chapters)
            cover = None
            if options.cover:
                cover = fx.prepare_cover(
                    self.ff, Path(options.cover), output_dir / f"{basename}.capa.jpg"
                )
                files["cover"] = cover.name

            # -- encode -------------------------------------------------------- #
            formats = [fx.FORMATS[f] for f in options.formats if f in fx.FORMATS]
            for i, fmt in enumerate(formats, 1):
                if not self.ff.has_encoder(fmt.encoder):
                    logger.warning(
                        "Encoder %s indisponível; formato %s ignorado", fmt.encoder, fmt.key
                    )
                    continue
                step(0.82 + 0.08 * i / max(1, len(formats)), f"Codificando {fmt.key.upper()}")
                dst = output_dir / f"{basename}.{fmt.ext}"
                fx.encode(
                    self.ff,
                    master,
                    dst,
                    fmt,
                    channels=mixed.channels,
                    bitrate=options.bitrate,
                    metadata_file=metadata_file,
                    cover=cover,
                    sample_rate=options.sample_rate,
                    speech_only=not options.music,
                )
                files[fmt.key] = dst.name
            if not any(k in files for k in fx.FORMATS):
                raise PipelineError("Nenhum formato de áudio pôde ser gerado (encoders ausentes).")
            lap("codificacao")

            # -- subtitles / transcript ---------------------------------------- #
            step(0.91, "Gerando legendas e transcrição")
            cues = build_cues(chunk_timings)
            srt_path = work / "legendas.srt"
            srt_path.write_text(to_srt(cues), encoding="utf-8")
            if options.subtitles:
                (output_dir / f"{basename}.srt").write_text(to_srt(cues), encoding="utf-8")
                (output_dir / f"{basename}.vtt").write_text(to_vtt(cues), encoding="utf-8")
                files["srt"] = f"{basename}.srt"
                files["vtt"] = f"{basename}.vtt"
            transcript_path = output_dir / f"{basename}.transcricao.json"
            transcript_path.write_text(
                json.dumps(
                    {
                        "title": title,
                        "duration": duration,
                        "sentences": transcript(chunk_timings),
                        "chapters": [{"start": s, "end": e, "title": t} for s, e, t in chapters],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            files["transcript"] = transcript_path.name

            # -- waveform ------------------------------------------------------ #
            if options.waveform:
                step(0.93, "Desenhando forma de onda")
                png = fx.waveform_png(self.ff, master, output_dir / f"{basename}.onda.png")
                files["waveform"] = png.name
                peaks_path = output_dir / f"{basename}.picos.json"
                peaks_path.write_text(
                    json.dumps(fx.waveform_peaks(self.ff, master)), encoding="utf-8"
                )
                files["peaks"] = peaks_path.name
            lap("extras")

            # -- video ----------------------------------------------------------- #
            if options.video:
                step(0.94, "Renderizando vídeo (pode levar alguns minutos)")
                background = options.background or options.cover
                video = fx.render_video(
                    self.ff,
                    master,
                    output_dir / f"{basename}.mp4",
                    workdir=work / "video",
                    orientation=options.video,
                    style=options.video_style,
                    title=title,
                    subtitles=srt_path if options.burn_subtitles else None,
                    background=Path(background) if background else None,
                )
                files["video"] = video.name
                lap("video")

            step(0.99, "Medindo o resultado")
            final = fx.measure_loudness(
                self.ff, master, target=options.loudness or -16.0, true_peak=options.true_peak
            )
            report = {
                "title": title,
                "basename": basename,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "version": __version__,
                "engine": engine.name,
                "engine_label": engine.label,
                "voice": voice,
                "fallbacks": fallbacks,
                "lang": options.lang,
                "characters": char_count,
                "words": len(doc.text.split()),
                "chunks": len(segments),
                "cache_hits": hits,
                "chapters": len(chapters),
                "subtitle_cues": len(cues),
                "duration": round(duration, 2),
                "channels": mixed.channels,
                "sample_rate": options.sample_rate,
                "loudness": {
                    "target": options.loudness,
                    "true_peak_target": options.true_peak,
                    "integrated": _num(final, "input_i"),
                    "true_peak": _num(final, "input_tp"),
                    "lra": _num(final, "input_lra"),
                },
                "ffmpeg": self.ff.version,
                "elapsed": round(time.monotonic() - started, 2),
                "steps": timings,
                "options": options.to_dict(),
            }
            report_path = output_dir / f"{basename}.relatorio.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            files["report"] = report_path.name
            step(1.0, "Concluído")
            return JobResult(output_dir, files, report)
        except fx.FFmpegError as exc:
            logger.debug("stderr do FFmpeg:\n%s", exc.stderr)
            raise PipelineError(f"Erro no processamento com FFmpeg: {exc}") from exc
        finally:
            if not os.environ.get("TTA_KEEP_WORK"):
                shutil.rmtree(work, ignore_errors=True)
            self.cache.prune()

    # ------------------------------------------------------------------ #

    def _synthesize(self, segments, options, lexicon, work, step):
        preferred = options.engine or "auto"
        voice_engine = find_voice_engine(options.voice, self.settings) if options.voice else None
        if preferred == "auto" and voice_engine:
            preferred = voice_engine
        try:
            chain = engine_chain(preferred, self.settings, fallback=options.fallback)
        except EngineUnavailable as exc:
            raise PipelineError(str(exc)) from exc
        if not chain:
            raise PipelineError(
                "Nenhum motor de voz disponível. Rode 'tta doctor' para ver o que falta instalar."
            )
        errors: list[str] = []
        for engine in chain:
            if options.voice and (voice_engine in (None, engine.name)) and engine.name == preferred:
                voice = options.voice
            else:
                voice = engine.default_voice(options.lang)
            if not voice:
                errors.append(f"{engine.label}: sem voz para o idioma {options.lang}")
                continue
            step(0.05, f"Sintetizando com {engine.label} ({voice})")
            try:
                results, hits = self._synth_with(
                    engine, voice, segments, options, lexicon, work, step
                )
                return engine, voice, results, errors, hits
            except JobCancelled:
                raise
            except EngineError as exc:
                logger.warning("%s falhou: %s", engine.label, exc)
                errors.append(f"{engine.label}: {exc}")
        raise PipelineError("Todos os motores de voz falharam:\n- " + "\n- ".join(errors))

    def _synth_with(
        self,
        engine: TTSEngine,
        voice: str,
        segments: list[Segment],
        options: JobOptions,
        lexicon: dict[str, str],
        work: Path,
        step,
    ) -> tuple[dict[int, SynthesisResult], int]:
        rate = options.rate if engine.native_rate else 0
        signature = engine.signature()
        stop = threading.Event()

        def task(seg: Segment) -> tuple[int, SynthesisResult, bool]:
            spoken = apply_lexicon(seg.text, lexicon) if lexicon else seg.text
            key = self.cache.key(signature, voice, rate, spoken)
            cached = self.cache.get(key, engine.output_ext)
            if cached:
                path, meta = cached
                sentences = [tuple(s) for s in meta.get("sentences", [])]
                return seg.index, SynthesisResult(path, sentences), True
            out = work / f"raw_{seg.index:05d}.{engine.output_ext}"
            for attempt in range(RETRIES):
                if stop.is_set():
                    raise EngineError("interrompido")
                try:
                    result = engine.synthesize(spoken, voice, out, rate)
                    break
                except EngineUnavailable:
                    raise
                except EngineError:
                    if attempt == RETRIES - 1:
                        raise
                    time.sleep(1.5 * 2**attempt)
            self.cache.put(key, engine.output_ext, result.path, {"sentences": result.sentences})
            return seg.index, result, False

        results: dict[int, SynthesisResult] = {}
        hits = 0
        workers = max(1, min(self.settings.max_workers, engine.max_concurrency, len(segments)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(task, seg) for seg in segments]
            try:
                for done, future in enumerate(as_completed(futures), 1):
                    index, result, hit = future.result()
                    results[index] = result
                    hits += hit
                    step(
                        0.05 + 0.55 * done / len(segments),
                        f"Sintetizando voz: {done}/{len(segments)}",
                    )
            except BaseException:
                stop.set()
                for future in futures:
                    future.cancel()
                raise
        return results, hits

    @staticmethod
    def _chapters(doc: Document, timings: list[ChunkTiming], duration: float):
        if not doc.has_chapters:
            return []
        starts: list[tuple[float, str]] = []
        seen: set[int] = set()
        for timing in timings:
            section = timing.extra.get("section")
            if section in seen:
                continue
            seen.add(section)
            title = timing.section_title or doc.title or "Início"
            starts.append((timing.start if starts else 0.0, title))
        chapters = []
        for i, (start, title) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else duration
            if end > start:
                chapters.append((round(start, 3), round(end, 3), title))
        return chapters


def _num(data: dict | None, key: str) -> float | None:
    try:
        return round(float(data[key]), 2) if data else None
    except (KeyError, TypeError, ValueError):
        return None
