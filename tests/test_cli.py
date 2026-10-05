import json
import subprocess
import sys
from pathlib import Path

import pytest

from texto_to_audio import cli

from conftest import needs_ffmpeg

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TTA_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_help_and_version(capsys):
    assert cli.main([]) == 0
    assert "synth" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--version"])


def test_presets_command(capsys, env):
    assert cli.main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "audiobook" in out and "podcast" in out


def test_doctor_json(capsys, env):
    code = cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert {e["name"] for e in report["engines"]} >= {"edge", "gtts", "piper", "espeak"}
    assert code in (0, 1)


def test_voices_json(capsys, env, fake_engine):
    assert cli.main(["voices", "--engine", "fake", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "fake-voice"


@needs_ffmpeg
def test_synth_text_and_files(capsys, env, fake_engine):
    out = env / "saida"
    code = cli.main(
        [
            "synth",
            "-t",
            "Olá mundo.",
            "--engine",
            "fake",
            "-o",
            str(out),
            "--name",
            "teste",
            "--formats",
            "mp3,opus",
            "--no-waveform",
        ]
    )
    assert code == 0
    assert (out / "teste.mp3").exists() and (out / "teste.opus").exists()
    assert not (out / "teste.onda.png").exists()
    assert "Arquivos em" in capsys.readouterr().out

    doc1, doc2 = env / "um.md", env / "dois.txt"
    doc1.write_text("# Um\n\nPrimeiro.", encoding="utf-8")
    doc2.write_text("Segundo.", encoding="utf-8")
    assert (
        cli.main(
            [
                "synth",
                str(doc1),
                str(doc2),
                "--engine",
                "fake",
                "--preset",
                "rascunho",
                "-o",
                str(out / "lote"),
            ]
        )
        == 0
    )
    assert (out / "lote" / "um" / "um.mp3").exists()
    assert (out / "lote" / "dois" / "dois.mp3").exists()


def test_synth_missing_file_and_asset(env, fake_engine):
    assert cli.main(["synth", str(env / "nao-existe.txt"), "--engine", "fake"]) == 2
    with pytest.raises(SystemExit):
        cli.main(["synth", "-t", "x", "--music", str(env / "nada.mp3")])


@needs_ffmpeg
def test_legacy_launcher_still_works(env):
    # The legacy script can't see the test-only fake engine, so only check argument handling.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "src" / "tts_converter.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--no-play" in proc.stdout
