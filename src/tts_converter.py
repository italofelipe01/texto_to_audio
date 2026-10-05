#!/usr/bin/env python3
"""Compatibility launcher for the 1.x/2.x command line.

    python src/tts_converter.py "Olá, mundo" [-o audio.mp3] [--no-play]

New projects should use the ``tta`` command (see README).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from texto_to_audio.cli import setup_logging  # noqa: E402
from texto_to_audio.config import Settings  # noqa: E402
from texto_to_audio.ffmpeg import FORMATS  # noqa: E402
from texto_to_audio.pipeline import Pipeline, PipelineError  # noqa: E402
from texto_to_audio.player import play  # noqa: E402
from texto_to_audio.presets import JobOptions  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Converte texto em fala (TTS) e reproduz.")
    parser.add_argument("text", nargs="?", help="texto a converter")
    parser.add_argument(
        "-o", "--output", default="audio.mp3", help="arquivo de saída (padrão: audio.mp3)"
    )
    parser.add_argument("--no-play", action="store_true", help="não reproduzir após gerar")
    args = parser.parse_args(argv)
    setup_logging()

    text = args.text
    if not text:
        try:
            text = input("Insira sua mensagem a ser disponibilizada em audio: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nOperação cancelada.")
            return 130
        if not text:
            print("Nenhum texto informado.", file=sys.stderr)
            return 2

    output = Path(args.output)
    fmt = output.suffix.lstrip(".").lower() or "mp3"
    if fmt not in FORMATS:
        fmt = "mp3"
    options = JobOptions.from_dict(
        {"formats": [fmt], "subtitles": False, "waveform": False, "chapters": False}
    )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = Pipeline(Settings.from_env()).run(
                options=options, output_dir=Path(tmp), text=text
            )
        except PipelineError as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 1
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.path(fmt)), output)
    print(f"Áudio salvo em: {output}")
    if not args.no_play:
        play(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
