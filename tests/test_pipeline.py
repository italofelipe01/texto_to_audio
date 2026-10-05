import json
import threading

import pytest

from texto_to_audio.pipeline import JobCancelled, Pipeline, PipelineError, slugify
from texto_to_audio.presets import JobOptions

from conftest import make_tone, needs_ffmpeg

pytestmark = needs_ffmpeg

TEXT = """# Livro de Teste

## Capítulo 1

Olá mundo. Esta é uma frase de teste.

## Capítulo 2

Segundo capítulo, com mais texto para legendas.
"""


def test_slugify():
    assert slugify("Ação & Reação: Capítulo 1!") == "acao-reacao-capitulo-1"
    assert slugify("???") == "audio"


def test_full_pipeline_outputs(settings, fake_engine, tmp_path):
    pipeline = Pipeline(settings)
    options = JobOptions.from_dict(
        {"engine": "fake", "formats": ["mp3", "m4b"], "rate": 10, "pitch": 1}
    )
    progress = []
    result = pipeline.run(
        options=options,
        output_dir=tmp_path / "out",
        text=TEXT,
        progress=lambda p, m: progress.append(p),
    )
    files = result.files
    for kind in ("mp3", "m4b", "srt", "vtt", "transcript", "waveform", "peaks", "report"):
        assert (result.output_dir / files[kind]).exists(), kind
    assert files["mp3"] == "livro-de-teste.mp3"
    report = result.report
    assert report["engine"] == "fake"
    assert report["chapters"] == 3
    assert report["loudness"]["integrated"] == pytest.approx(-16, abs=1.0)
    assert progress == sorted(progress) and progress[-1] == 1.0
    chapters = pipeline.ff.probe(result.path("m4b"))["chapters"]
    assert [c["tags"]["title"] for c in chapters] == ["Livro de Teste", "Capítulo 1", "Capítulo 2"]
    transcript = json.loads(result.path("transcript").read_text(encoding="utf-8"))
    starts = [s["start"] for s in transcript["sentences"]]
    assert starts == sorted(starts)
    assert transcript["duration"] == pytest.approx(report["duration"], abs=0.1)
    assert not (result.output_dir / ".trabalho").exists()


def test_cache_avoids_resynthesis(settings, fake_engine, tmp_path):
    pipeline = Pipeline(settings)
    options = JobOptions.from_dict({"engine": "fake", "preset": "rascunho"})
    first = pipeline.run(options=options, output_dir=tmp_path / "a", text=TEXT)
    calls = len(fake_engine.calls)
    second = pipeline.run(options=options, output_dir=tmp_path / "b", text=TEXT)
    assert len(fake_engine.calls) == calls
    assert second.report["cache_hits"] == first.report["chunks"]


def test_fallback_to_next_engine(settings, fake_engine, tmp_path, monkeypatch):
    monkeypatch.setattr("texto_to_audio.pipeline.time.sleep", lambda _s: None)
    options = JobOptions.from_dict({"engine": "broken", "preset": "rascunho"})
    result = Pipeline(settings).run(options=options, output_dir=tmp_path, text="Olá.")
    assert result.report["engine"] == "fake"
    assert "fora do ar" in result.report["fallbacks"][0]


def test_all_engines_failing_reports_each_error(settings, fake_engine, tmp_path, monkeypatch):
    monkeypatch.setattr("texto_to_audio.pipeline.time.sleep", lambda _s: None)
    options = JobOptions.from_dict({"engine": "broken", "fallback": False, "preset": "rascunho"})
    with pytest.raises(PipelineError, match="Todos os motores"):
        Pipeline(settings).run(options=options, output_dir=tmp_path, text="Olá.")


def test_lexicon_changes_spoken_text_only(settings, fake_engine, tmp_path):
    options = JobOptions.from_dict({"engine": "fake", "lexicon": {"TTS": "tê tê ésse"}})
    result = Pipeline(settings).run(options=options, output_dir=tmp_path, text="O TTS funciona.")
    assert fake_engine.calls == ["O tê tê ésse funciona."]
    assert "O TTS funciona." in result.path("srt").read_text(encoding="utf-8")


def test_global_lexicon_file(settings, fake_engine, tmp_path):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "pronuncia.txt").write_text("IA = i a\n", encoding="utf-8")
    Pipeline(settings).run(
        options=JobOptions.from_dict({"engine": "fake", "preset": "rascunho"}),
        output_dir=tmp_path,
        text="A IA fala.",
    )
    assert fake_engine.calls == ["A i a fala."]


def test_music_intro_video_and_cover(settings, fake_engine, tmp_path):
    music = make_tone(tmp_path / "music.wav", 1.0, freq=330)
    intro = make_tone(tmp_path / "intro.wav", 0.4, freq=550)
    options = JobOptions.from_dict(
        {
            "engine": "fake",
            "music": str(music),
            "intro": str(intro),
            "video": "square",
            "formats": ["mp3"],
        }
    )
    result = Pipeline(settings).run(
        options=options, output_dir=tmp_path / "out", text="Uma frase curta."
    )
    assert result.report["channels"] == 2
    assert result.path("video").exists()
    cues = result.path("vtt").read_text(encoding="utf-8")
    # voice starts after the intro and the music lead-in, so the first cue is not at zero
    assert "00:00:00.000 -->" not in cues


def test_cancel(settings, fake_engine, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(JobCancelled):
        Pipeline(settings).run(
            options=JobOptions.from_dict({"engine": "fake"}),
            output_dir=tmp_path,
            text="Olá.",
            cancel=cancel,
        )


def test_input_errors(settings, fake_engine, tmp_path):
    pipeline = Pipeline(settings)
    options = JobOptions.from_dict({"engine": "fake"})
    with pytest.raises(PipelineError, match="Nenhum texto"):
        pipeline.run(options=options, output_dir=tmp_path, text="   ")
    settings.max_chars = 10
    with pytest.raises(PipelineError, match="muito longo"):
        pipeline.run(options=options, output_dir=tmp_path, text="x" * 50)
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf")
    settings.max_chars = 1000
    with pytest.raises(PipelineError):
        pipeline.run(options=options, output_dir=tmp_path, source=bad)
