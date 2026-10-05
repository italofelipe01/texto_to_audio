import json

from texto_to_audio.pipeline import Pipeline
from texto_to_audio.watch import watch

from conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def test_watch_once_processes_and_moves_files(settings, fake_engine, tmp_path):
    inbox = tmp_path / "entrada"
    out = tmp_path / "saida"
    inbox.mkdir()
    (inbox / "capitulo.md").write_text("# Capítulo\n\nTexto do capítulo.", encoding="utf-8")
    (inbox / "capitulo.json").write_text(json.dumps({"formats": ["opus"], "title": "Meu título"}))
    (inbox / "quebrado.pdf").write_bytes(b"isto nao e pdf")
    (inbox / "ignorar.xyz").write_text("x")

    count = watch(
        inbox, out, {"engine": "fake", "preset": "rascunho"}, Pipeline(settings), once=True
    )

    assert count == 2
    assert (out / "capitulo" / "meu-titulo.opus").exists()
    assert (inbox / "processados" / "capitulo.md").exists()
    assert (inbox / "processados" / "capitulo.json").exists()
    assert (inbox / "falhas" / "quebrado.pdf").exists()
    assert "PDF" in (inbox / "falhas" / "quebrado.pdf.erro.txt").read_text(encoding="utf-8")
    assert (inbox / "ignorar.xyz").exists()
