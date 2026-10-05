import sys
import types
from pathlib import Path

import pytest

from texto_to_audio import engines
from texto_to_audio.engines import EngineError, engine_chain, find_voice_engine, get_engine
from texto_to_audio.engines.base import locale_matches


def test_locale_matching():
    assert locale_matches("pt-BR", "pt")
    assert locale_matches("pt_BR", "pt-br")
    assert not locale_matches("pt-PT", "pt-BR")
    assert locale_matches("en-US", None)


def test_chain_auto_order_and_explicit_preference(settings, fake_engine):
    chain = [e.name for e in engine_chain("auto", settings)]
    assert chain[:2] == ["broken", "fake"] or chain[:2] == ["fake", "broken"]
    chain = [e.name for e in engine_chain("fake", settings)]
    assert chain[0] == "fake" and len(chain) > 1
    assert [e.name for e in engine_chain("fake", settings, fallback=False)] == ["fake"]


def test_unknown_engine_raises_helpful_error(settings):
    with pytest.raises(EngineError, match="desconhecido"):
        engine_chain("inexistente", settings)


def test_find_voice_engine(settings):
    assert find_voice_engine("pt-BR-AntonioNeural", settings) == "edge"
    assert find_voice_engine("pt_BR-faber-medium", settings) == "piper"
    assert find_voice_engine("pt-PT", settings) == "gtts"
    assert find_voice_engine("pt-br", settings) == "espeak"
    assert find_voice_engine("???", settings) is None


def test_get_engine_is_cached_per_settings(settings):
    assert get_engine("gtts", settings) is get_engine("gtts", settings)


def test_edge_engine_with_mocked_service(settings, monkeypatch, tmp_path):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            captured.update(text=text, voice=voice, **kwargs)

        async def stream(self):
            yield {
                "type": "SentenceBoundary",
                "offset": 1_000_000,
                "duration": 5_000_000,
                "text": "Olá.",
            }
            yield {"type": "audio", "data": b"ID3fake-mp3"}

    fake_module = types.SimpleNamespace(Communicate=FakeCommunicate)
    monkeypatch.setitem(sys.modules, "edge_tts", fake_module)
    engine = get_engine("edge", settings)
    out = tmp_path / "x.mp3"
    result = engine.synthesize("Olá.", "pt-BR-FranciscaNeural", out, rate=-10)
    assert out.read_bytes() == b"ID3fake-mp3"
    assert captured["rate"] == "-10%"
    assert captured["voice"] == "pt-BR-FranciscaNeural"
    assert result.sentences == [(0.1, 0.6, "Olá.")]


def test_edge_engine_wraps_network_errors(settings, monkeypatch, tmp_path):
    class FailingCommunicate:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            raise OSError("sem rede")
            yield  # pragma: no cover

    monkeypatch.setitem(
        sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FailingCommunicate)
    )
    with pytest.raises(EngineError, match="sem rede"):
        get_engine("edge", settings).synthesize("x", "pt-BR-FranciscaNeural", tmp_path / "x.mp3")


def test_edge_voice_list_falls_back_offline(settings, monkeypatch):
    async def boom(**_kwargs):
        raise OSError("offline")

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(list_voices=boom))
    voices = get_engine("edge", settings).list_voices("pt-BR")
    assert {v.id for v in voices} >= {"pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"}


def test_gtts_engine_uses_accent(settings, monkeypatch, tmp_path):
    calls = {}

    class FakeGTTS:
        def __init__(self, text, lang, tld):
            calls.update(text=text, lang=lang, tld=tld)

        def save(self, path):
            Path(path).write_bytes(b"mp3")

    monkeypatch.setitem(sys.modules, "gtts", types.SimpleNamespace(gTTS=FakeGTTS))
    engine = get_engine("gtts", settings)
    engine.synthesize("Oi", "pt-PT", tmp_path / "a.mp3")
    assert calls == {"text": "Oi", "lang": "pt", "tld": "pt"}
    assert engine.default_voice("pt-BR") == "pt-BR"
    with pytest.raises(EngineError):
        engine.synthesize("Oi", "klingon", tmp_path / "b.mp3")


def test_espeak_command(settings, monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("-w") + 1]).write_bytes(b"\0" * 100)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.run", fake_run)
    engine = get_engine("espeak", settings)
    engine.synthesize("Olá", "pt-br", tmp_path / "e.wav", rate=100)
    assert seen["cmd"][:5] == ["/usr/bin/espeak-ng", "-v", "pt-br", "-s", "330"]


def test_piper_lists_installed_and_downloadable_voices(settings, tmp_path):
    folder = settings.piper_voices_dirs[0]
    folder.mkdir(parents=True)
    (folder / "pt_BR-teste-medium.onnx").write_bytes(b"x")
    (folder / "pt_BR-teste-medium.onnx.json").write_text("{}")
    engine = get_engine("piper", settings)
    voices = {v.id: v for v in engine.list_voices("pt-BR")}
    assert voices["pt_BR-teste-medium"].installed
    assert not voices["pt_BR-faber-medium"].installed
    assert engine.default_voice("pt-BR") == "pt_BR-teste-medium"


def test_registry_restores_after_fixture():
    assert "fake" not in engines.engine_names()
