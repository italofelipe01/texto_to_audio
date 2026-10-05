"""Shared fixtures: an offline fake TTS engine so tests never need the internet."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from texto_to_audio import engines
from texto_to_audio.config import Settings
from texto_to_audio.engines.base import EngineError, SynthesisResult, TTSEngine, Voice

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg não instalado")


def make_tone(path: Path, seconds: float, freq: int = 220, rate: int = 24000) -> Path:
    """Speech-like test signal: modulated noise with silence padding on both ends."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=f={freq}:d={seconds}:sample_rate={rate}",
            "-af",
            "volume=0.5,adelay=150:all=1,apad=pad_dur=0.3",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


class FakeEngine(TTSEngine):
    name = "fake"
    label = "Fake (testes)"
    online = False
    native_rate = True
    output_ext = "wav"
    calls: list[str] = []
    fail_with: str | None = None

    def check(self):
        return True, "ok"

    def list_voices(self, locale=None):
        return [Voice(id="fake-voice", name="Fake", locale="pt-BR", engine=self.name)]

    def default_voice(self, locale):
        return "fake-voice"

    def synthesize(self, text, voice, out_path, rate=0):
        FakeEngine.calls.append(text)
        if FakeEngine.fail_with:
            raise EngineError(FakeEngine.fail_with)
        seconds = max(0.4, len(text) / 40)
        make_tone(out_path, seconds)
        return SynthesisResult(out_path, [])


class BrokenEngine(FakeEngine):
    name = "broken"
    label = "Broken (testes)"

    def synthesize(self, text, voice, out_path, rate=0):
        raise EngineError("serviço fora do ar")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", max_workers=2)


@pytest.fixture
def fake_engine():
    FakeEngine.calls = []
    FakeEngine.fail_with = None
    engines.register_engine(FakeEngine, priority=0)
    engines.register_engine(BrokenEngine, priority=0)
    yield FakeEngine
    engines.unregister_engine("fake")
    engines.unregister_engine("broken")
