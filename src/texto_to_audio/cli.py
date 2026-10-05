"""Command line interface: ``tta <comando>``."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import socket
import sys
from pathlib import Path

from . import __version__
from .config import Settings
from .engines import engine_names, get_engine
from .ffmpeg import FORMATS, VIDEO_SIZES, FFmpeg
from .pipeline import JobResult, Pipeline, PipelineError, slugify
from .presets import PRESETS, JobOptions
from .text import parse_lexicon

logger = logging.getLogger("texto_to_audio")


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _synth_options_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    voz = p.add_argument_group("voz")
    voz.add_argument(
        "--preset", choices=sorted(PRESETS), help="perfil pronto (audiobook, podcast, video...)"
    )
    voz.add_argument(
        "--engine", help=f"motor de voz: auto, {', '.join(engine_names())} (padrão: auto)"
    )
    voz.add_argument("--voice", help="voz (ex.: pt-BR-FranciscaNeural, pt_BR-faber-medium)")
    voz.add_argument("--lang", help="idioma (padrão: pt-BR)")
    voz.add_argument("--rate", type=int, help="velocidade em %% (-50 a 100)")
    voz.add_argument("--pitch", type=float, help="tom em semitons (-12 a 12)")
    voz.add_argument(
        "--no-fallback", action="store_true", help="não trocar de motor se o escolhido falhar"
    )
    voz.add_argument(
        "--lexicon", type=Path, help="dicionário de pronúncia (linhas 'termo = como falar')"
    )

    pos = p.add_argument_group("pós-produção (FFmpeg)")
    pos.add_argument("--formats", help=f"formatos separados por vírgula: {', '.join(FORMATS)}")
    pos.add_argument("--loudness", help="alvo em LUFS (ex.: -16) ou 'off'")
    pos.add_argument("--true-peak", type=float, help="pico máximo em dBTP (ex.: -1.5)")
    pos.add_argument(
        "--no-enhance", action="store_true", help="desativa EQ/compressor/de-esser na voz"
    )
    pos.add_argument(
        "--no-trim", action="store_true", help="não remove silêncios nas pontas dos trechos"
    )
    pos.add_argument("--music", type=Path, help="trilha de fundo (com ducking automático)")
    pos.add_argument("--music-gain", type=float, help="volume da trilha em dB (padrão: -20)")
    pos.add_argument(
        "--no-ducking", action="store_true", help="não abaixar a trilha quando há fala"
    )
    pos.add_argument("--intro", type=Path, help="vinheta de abertura")
    pos.add_argument("--outro", type=Path, help="vinheta de encerramento")
    pos.add_argument("--bitrate", help="bitrate dos formatos com perdas (ex.: 128k)")

    out = p.add_argument_group("saídas")
    out.add_argument("-o", "--output", type=Path, help="pasta de saída")
    out.add_argument("--name", help="nome base dos arquivos gerados")
    out.add_argument(
        "--video", choices=sorted(VIDEO_SIZES), help="gera MP4 (landscape, portrait, square)"
    )
    out.add_argument(
        "--video-style", choices=("waves", "bars", "none"), help="visualização no vídeo"
    )
    out.add_argument("--background", type=Path, help="imagem de fundo do vídeo")
    out.add_argument("--cover", type=Path, help="capa embutida no áudio (e fundo do vídeo)")
    out.add_argument("--no-subtitles", action="store_true", help="não gerar SRT/VTT")
    out.add_argument("--no-burn", action="store_true", help="não embutir legendas no vídeo")
    out.add_argument("--no-chapters", action="store_true", help="não gerar capítulos")
    out.add_argument(
        "--no-waveform", action="store_true", help="não gerar imagem/picos da forma de onda"
    )

    meta = p.add_argument_group("metadados")
    meta.add_argument("--title", help="título")
    meta.add_argument("--artist", help="autor/narrador")
    meta.add_argument("--album", help="álbum/série")
    return p


def options_from_args(args: argparse.Namespace) -> dict:
    lexicon = None
    if getattr(args, "lexicon", None):
        lexicon = parse_lexicon(Path(args.lexicon).read_text(encoding="utf-8"))
    data = {
        "preset": args.preset,
        "engine": args.engine,
        "voice": args.voice,
        "lang": args.lang,
        "rate": args.rate,
        "pitch": args.pitch,
        "fallback": False if args.no_fallback else None,
        "lexicon": lexicon,
        "formats": args.formats,
        "loudness": args.loudness,
        "true_peak": args.true_peak,
        "enhance": False if args.no_enhance else None,
        "trim_silence": False if args.no_trim else None,
        "music": str(args.music) if args.music else None,
        "music_gain_db": args.music_gain,
        "ducking": False if args.no_ducking else None,
        "intro": str(args.intro) if args.intro else None,
        "outro": str(args.outro) if args.outro else None,
        "bitrate": args.bitrate,
        "basename": args.name,
        "video": args.video,
        "video_style": args.video_style,
        "background": str(args.background) if args.background else None,
        "cover": str(args.cover) if args.cover else None,
        "subtitles": False if args.no_subtitles else None,
        "burn_subtitles": False if args.no_burn else None,
        "chapters": False if args.no_chapters else None,
        "waveform": False if args.no_waveform else None,
        "title": args.title,
        "artist": args.artist,
        "album": args.album,
    }
    if isinstance(data["loudness"], str) and data["loudness"].lower() not in ("off", "none"):
        try:
            data["loudness"] = float(data["loudness"])
        except ValueError as exc:
            raise SystemExit(f"--loudness inválido: {args.loudness}") from exc
    for key in ("music", "intro", "outro", "background", "cover"):
        if data[key] and not Path(data[key]).exists():
            raise SystemExit(f"Arquivo não encontrado para --{key}: {data[key]}")
    return {k: v for k, v in data.items() if v is not None}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tta",
        description="Texto para Áudio: vozes neurais gratuitas + pós-produção com FFmpeg.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra detalhes (debug)")
    parser.add_argument("-q", "--quiet", action="store_true", help="mostra só erros")
    sub = parser.add_subparsers(dest="command", metavar="<comando>")

    synth_parent = _synth_options_parser()
    synth = sub.add_parser(
        "synth",
        parents=[synth_parent],
        help="converte texto/arquivos em áudio",
        description="Converte texto ou arquivos (.txt .md .html .docx .odt .epub .pdf) em áudio.",
    )
    synth.add_argument(
        "inputs", nargs="*", help="arquivos de entrada ou '-' para ler da entrada padrão"
    )
    synth.add_argument("-t", "--text", help="texto a converter")
    synth.add_argument("--play", action="store_true", help="reproduz o áudio ao terminar (ffplay)")

    voices = sub.add_parser("voices", help="lista as vozes disponíveis")
    voices.add_argument("--engine", help="filtra por motor")
    voices.add_argument(
        "--lang", default="pt", help="filtra por idioma (padrão: pt; use 'all' para todos)"
    )
    voices.add_argument("--json", action="store_true")

    doctor = sub.add_parser(
        "doctor", help="diagnóstico do ambiente (FFmpeg, motores, dependências)"
    )
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--online", action="store_true", help="testa também a conexão com os serviços de voz"
    )

    sub.add_parser("presets", help="lista os perfis prontos")

    watch = sub.add_parser(
        "watch", parents=[synth_parent], help="monitora uma pasta e converte o que chegar"
    )
    watch.add_argument(
        "inbox", type=Path, nargs="?", help="pasta de entrada (padrão: data/entrada)"
    )
    watch.add_argument(
        "--interval", type=float, default=5.0, help="intervalo de verificação em segundos"
    )
    watch.add_argument("--once", action="store_true", help="processa o que existe e sai")

    serve = sub.add_parser("serve", help="inicia a interface web")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    download = sub.add_parser("download-voice", help="baixa um modelo de voz Piper (offline)")
    download.add_argument("voices", nargs="+", help="ex.: pt_BR-faber-medium")

    cache = sub.add_parser("cache", help="gerencia o cache de trechos sintetizados")
    cache.add_argument("action", choices=("info", "clear", "prune"))
    return parser


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


class _Progress:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.last = ""

    def __call__(self, fraction: float, message: str) -> None:
        if not self.enabled:
            return
        width = 28
        filled = int(width * fraction)
        line = (
            f"\r[{'#' * filled}{'.' * (width - filled)}] {int(fraction * 100):3d}% {message[:60]}"
        )
        sys.stderr.write(line.ljust(len(self.last)))
        sys.stderr.flush()
        self.last = line
        if fraction >= 1:
            sys.stderr.write("\n")

    def close(self) -> None:
        if self.enabled and self.last:
            sys.stderr.write("\n")
            self.last = ""


def _print_result(result: JobResult) -> None:
    report = result.report
    loud = report["loudness"]
    minutes, seconds = divmod(int(round(report["duration"])), 60)
    print(f"\n✔ {report['title']} — {minutes}min{seconds:02d}s")
    print(f"  Voz: {report['voice']} ({report['engine_label']})")
    if report["fallbacks"]:
        print("  Motores que falharam antes: " + "; ".join(report["fallbacks"]))
    if loud.get("integrated") is not None:
        print(f"  Loudness: {loud['integrated']} LUFS, pico {loud['true_peak']} dBTP")
    print(
        f"  Trechos: {report['chunks']} (cache: {report['cache_hits']}), tempo: {report['elapsed']}s"
    )
    print(f"  Arquivos em {result.output_dir}:")
    for kind, name in result.files.items():
        print(f"    - {kind:<10} {name}")


def cmd_synth(args: argparse.Namespace, settings: Settings) -> int:
    options = JobOptions.from_dict({"lang": settings.default_lang, **options_from_args(args)})
    if options.engine == "auto" and settings.default_engine != "auto" and not args.engine:
        options.engine = settings.default_engine
    pipeline = Pipeline(settings)
    jobs: list[tuple[str | None, Path | None]] = []
    if args.text:
        jobs.append((args.text, None))
    for item in args.inputs:
        if item == "-":
            jobs.append((sys.stdin.read(), None))
        else:
            path = Path(item)
            if not path.exists():
                print(f"Arquivo não encontrado: {item}", file=sys.stderr)
                return 2
            jobs.append((None, path))
    if not jobs:
        if not sys.stdin.isatty():
            jobs.append((sys.stdin.read(), None))
        else:
            try:
                text = input("Digite o texto a ser convertido em áudio: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nOperação cancelada.")
                return 130
            if not text:
                print("Nenhum texto informado.", file=sys.stderr)
                return 2
            jobs.append((text, None))

    exit_code = 0
    for text, source in jobs:
        if args.output and len(jobs) == 1:
            output_dir = args.output
        else:
            name = source.stem if source else slugify(options.title or (text or "")[:40])
            output_dir = (args.output or Path(settings.output_dir)) / slugify(name)
        progress = _Progress(sys.stderr.isatty() and not args.quiet)
        try:
            result = pipeline.run(
                options=options, output_dir=output_dir, text=text, source=source, progress=progress
            )
        except PipelineError as exc:
            progress.close()
            print(f"✘ {exc}", file=sys.stderr)
            exit_code = 1
            continue
        except KeyboardInterrupt:
            progress.close()
            print("\nCancelado.", file=sys.stderr)
            return 130
        progress.close()
        if not args.quiet:
            _print_result(result)
        if args.play:
            from .player import play

            audio = next((result.path(k) for k in FORMATS if k in result.files), None)
            if audio:
                play(audio)
    return exit_code


def cmd_voices(args: argparse.Namespace, settings: Settings) -> int:
    lang = None if args.lang in ("all", "todos", "*") else args.lang
    names = [args.engine] if args.engine else engine_names()
    rows = []
    for name in names:
        engine = get_engine(name, settings)
        ok, reason = engine.check()
        if not ok:
            if not args.json:
                print(f"# {engine.label}: indisponível — {reason}")
            continue
        rows += [v.to_dict() for v in engine.list_voices(lang)]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    current = None
    for row in rows:
        if row["engine"] != current:
            current = row["engine"]
            print(f"\n== {get_engine(current, settings).label} ==")
        flags = []
        if row.get("gender"):
            flags.append(
                {"Female": "feminina", "Male": "masculina"}.get(row["gender"], row["gender"])
            )
        if not row.get("installed", True):
            flags.append("baixa no 1º uso")
        print(f"  {row['id']:<38} {row['locale']:<6} {', '.join(flags)}")
    return 0


def _probe_host(host: str, port: int = 443, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def doctor_report(settings: Settings, *, online: bool = False) -> dict:
    ff = FFmpeg(settings.ffmpeg_bin, settings.ffprobe_bin)
    optional = {}
    for module in ("fastapi", "uvicorn", "multipart", "pypdf", "piper", "edge_tts", "gtts"):
        try:
            __import__(module)
            optional[module] = True
        except ImportError:
            optional[module] = False
    report = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ffmpeg": ff.capabilities(),
        "engines": [get_engine(n, settings).info() for n in engine_names()],
        "modules": optional,
        "paths": {
            "data": str(settings.data_dir),
            "output": str(settings.output_dir),
            "cache": str(settings.cache_dir),
            "piper_voices": [str(p) for p in settings.piper_voices_dirs],
        },
    }
    if online:
        report["network"] = {
            "edge (speech.platform.bing.com)": _probe_host("speech.platform.bing.com"),
            "gtts (translate.google.com)": _probe_host("translate.google.com"),
            "piper (huggingface.co)": _probe_host("huggingface.co"),
        }
    return report


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    report = doctor_report(settings, online=args.online)
    ff = report["ffmpeg"]
    ok_engines = [e for e in report["engines"] if e["available"]]
    healthy = ff["available"] and bool(ok_engines)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if healthy else 1

    def mark(flag: bool) -> str:
        return "✔" if flag else "✘"

    print(
        f"Texto para Áudio {report['version']} — Python {report['python']} — {report['platform']}"
    )
    print(f"\n{mark(ff['available'])} FFmpeg {ff['version'] or 'NÃO ENCONTRADO'}")
    if ff["available"]:
        for name, has in ff["filters"].items():
            print(f"    {mark(has)} {name:<18} {ff['filter_descriptions'][name]}")
        print("    Formatos: " + ", ".join(f"{k} {mark(v)}" for k, v in ff["formats"].items()))
        print(f"    Vídeo MP4: {mark(ff['video'])}   Reamostragem SoX: {mark(ff['soxr'])}")
    else:
        print(
            "    Instale: sudo apt install ffmpeg | brew install ffmpeg | winget install Gyan.FFmpeg"
        )
    print("\nMotores de voz (ordem de preferência no modo auto):")
    for engine in report["engines"]:
        print(
            f"    {mark(engine['available'])} {engine['name']:<7} {engine['label']:<28} {engine['reason']}"
        )
    modules = report["modules"]
    print("\nRecursos opcionais:")
    print(
        f"    {mark(modules['fastapi'] and modules['uvicorn'] and modules['multipart'])} interface web  (pip install texto-to-audio[web])"
    )
    print(f"    {mark(modules['pypdf'])} leitura de PDF (pip install texto-to-audio[pdf])")
    print(f"    {mark(modules['piper'])} voz offline Piper (pip install texto-to-audio[piper])")
    if "network" in report:
        print("\nConectividade:")
        for name, reachable in report["network"].items():
            print(f"    {mark(reachable)} {name}")
    print(f"\nDados em: {report['paths']['data']}")
    print("\nTudo pronto!" if healthy else "\nCorrija os itens marcados com ✘ acima.")
    return 0 if healthy else 1


def cmd_presets(_args: argparse.Namespace, _settings: Settings) -> int:
    for preset in PRESETS.values():
        print(f"{preset.name:<15} {preset.label}\n{'':<15} {preset.description}")
    return 0


def cmd_watch(args: argparse.Namespace, settings: Settings) -> int:
    from .watch import watch

    inbox = args.inbox or Path(settings.data_dir) / "entrada"
    output = args.output or Path(settings.output_dir)
    base = {"lang": settings.default_lang, **options_from_args(args)}
    try:
        watch(inbox, output, base, Pipeline(settings), interval=args.interval, once=args.once)
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    try:
        import uvicorn
    except ImportError:
        print("A interface web requer: pip install 'texto-to-audio[web]'", file=sys.stderr)
        return 1
    from .web.app import create_app

    print(f"Interface web em http://{args.host}:{args.port}")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_download_voice(args: argparse.Namespace, settings: Settings) -> int:
    engine = get_engine("piper", settings)
    ok, reason = engine.check()
    if not ok and "não instalado" in reason:
        print(reason, file=sys.stderr)
        return 1
    for voice in args.voices:
        try:
            path = engine.download(voice)
        except Exception as exc:
            print(f"✘ {voice}: {exc}", file=sys.stderr)
            return 1
        print(f"✔ {voice} → {path}")
    return 0


def cmd_cache(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = Pipeline(settings)
    if args.action == "clear":
        pipeline.cache.clear()
        print("Cache apagado.")
    elif args.action == "prune":
        removed = pipeline.cache.prune()
        print(f"{removed} itens removidos.")
    else:
        size = pipeline.cache.size() / 1024 / 1024
        print(f"Cache em {settings.cache_dir}: {size:.1f} MB (limite {settings.cache_max_mb} MB)")
    return 0


COMMANDS = {
    "synth": cmd_synth,
    "voices": cmd_voices,
    "doctor": cmd_doctor,
    "presets": cmd_presets,
    "watch": cmd_watch,
    "serve": cmd_serve,
    "download-voice": cmd_download_voice,
    "cache": cmd_cache,
}


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    setup_logging(args.verbose, args.quiet)
    if args.command in ("watch", "serve"):
        logging.getLogger("texto_to_audio").setLevel(
            logging.DEBUG if args.verbose else logging.INFO
        )
    settings = Settings.from_env()
    return COMMANDS[args.command](args, settings)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
