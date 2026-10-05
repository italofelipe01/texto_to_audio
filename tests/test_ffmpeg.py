import json

import pytest

from texto_to_audio import ffmpeg as fx

from conftest import make_tone, needs_ffmpeg


def test_tempo_filters_chain_atempo_within_limits():
    assert fx.tempo_filters(1.0) == []
    assert fx.tempo_filters(3.0) == ["atempo=2.0", "atempo=1.5000"]
    assert fx.tempo_filters(0.3) == ["atempo=0.5", "atempo=0.6000"]
    assert fx.tempo_filters(1.2, rubberband=True) == ["rubberband=tempo=1.2000:pitchq=quality"]


def test_pitch_filters():
    assert fx.pitch_filters(0) == []
    assert fx.pitch_filters(12, rubberband=True)[0].startswith(
        "rubberband=pitch=2.00000:formant=preserved"
    )
    fallback = fx.pitch_filters(12, sample_rate=48000)
    assert fallback[0] == "asetrate=96000"
    assert fallback[1] == "aresample=48000"


def test_ffmetadata_escaping(tmp_path):
    path = tmp_path / "meta.txt"
    fx.write_ffmetadata(path, {"title": "A=B; #1"}, [(0, 1.5, "Cap; 1")])
    content = path.read_text(encoding="utf-8")
    assert content.startswith(";FFMETADATA1\n")
    assert r"title=A\=B\; \#1" in content
    assert "START=0\nEND=1500\n" + r"title=Cap\; 1" in content


@pytest.fixture
def ff():
    return fx.FFmpeg()


@needs_ffmpeg
def test_capabilities(ff):
    caps = ff.capabilities()
    assert caps["available"]
    assert caps["filters"]["loudnorm"]
    assert caps["formats"]["mp3"]


@needs_ffmpeg
def test_prepare_chunk_trims_and_pads(ff, tmp_path):
    src = make_tone(tmp_path / "raw.wav", 1.0)  # 0.15 s lead + 1 s tone + 0.3 s tail
    speech, total = fx.prepare_chunk(ff, src, tmp_path / "chunk.wav", pause=0.5)
    assert 1.0 <= speech <= 1.2
    assert total == pytest.approx(speech + 0.5, abs=0.02)
    speech_fast, _ = fx.prepare_chunk(ff, src, tmp_path / "fast.wav", tempo=2.0, trim=True)
    assert speech_fast == pytest.approx(speech / 2, abs=0.1)


@needs_ffmpeg
def test_mix_normalize_encode_with_chapters_and_cover(ff, tmp_path):
    voice = make_tone(tmp_path / "voice.wav", 4.0, rate=48000)
    music = make_tone(tmp_path / "music.wav", 1.0, freq=440)
    intro = make_tone(tmp_path / "intro.wav", 0.5, freq=660)
    mixed = fx.mix(ff, voice, tmp_path / "pre.wav", music=music, intro=intro, music_lead=1.0)
    assert mixed.channels == 2
    assert mixed.offset == pytest.approx(1.0 + ff.duration(intro), abs=0.05)
    master = tmp_path / "master.wav"
    fx.normalize_loudness(ff, mixed.path, master, target=-16, true_peak=-1.5)
    measured = fx.measure_loudness(ff, master)
    assert float(measured["input_i"]) == pytest.approx(-16, abs=1.0)

    cover_src = tmp_path / "cover.png"
    ff.run(["-f", "lavfi", "-i", "color=c=red:s=300x200", "-frames:v", "1", str(cover_src)])
    cover = fx.prepare_cover(ff, cover_src, tmp_path / "cover.jpg", size=200)
    meta = tmp_path / "meta.txt"
    fx.write_ffmetadata(meta, {"title": "Teste"}, [(0, 2, "Um"), (2, 4, "Dois")])
    for key in ("mp3", "m4b", "opus"):
        out = fx.encode(
            ff,
            master,
            tmp_path / f"out.{fx.FORMATS[key].ext}",
            fx.FORMATS[key],
            channels=2,
            metadata_file=meta,
            cover=cover,
        )
        info = ff.probe(out)
        assert len(info["chapters"]) == 2, key
        if fx.FORMATS[key].cover:
            assert any(s["codec_type"] == "video" for s in info["streams"]), key


@needs_ffmpeg
def test_waveform_outputs(ff, tmp_path):
    audio = make_tone(tmp_path / "a.wav", 2.0)
    png = fx.waveform_png(ff, audio, tmp_path / "w.png", width=400, height=80)
    assert png.stat().st_size > 0
    peaks = fx.waveform_peaks(ff, audio, buckets=100)
    assert 90 <= len(peaks) <= 100
    assert max(peaks) == 1.0
    json.dumps(peaks)


@needs_ffmpeg
@pytest.mark.parametrize("orientation,style", [("landscape", "waves"), ("portrait", "bars")])
def test_render_video(ff, tmp_path, orientation, style):
    audio = make_tone(tmp_path / "a.wav", 1.5)
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nLegenda com acentuação\n", encoding="utf-8")
    out = fx.render_video(
        ff,
        audio,
        tmp_path / "v.mp4",
        workdir=tmp_path / "work",
        orientation=orientation,
        style=style,
        title="Título",
        subtitles=srt,
    )
    info = ff.probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == fx.VIDEO_SIZES[orientation]
    assert float(info["format"]["duration"]) == pytest.approx(ff.duration(audio), abs=0.3)


def test_missing_ffmpeg_raises_friendly_error(tmp_path):
    ff = fx.FFmpeg("ffmpeg-que-nao-existe", "ffprobe-que-nao-existe")
    assert not ff.available()
    with pytest.raises(fx.FFmpegError, match="FFmpeg não encontrado"):
        ff.run(["-version"])
