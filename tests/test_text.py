import zipfile

import pytest

from texto_to_audio.text import (
    TextExtractionError,
    apply_lexicon,
    clean_for_speech,
    decode_bytes,
    estimate_duration,
    load_document,
    parse_html,
    parse_lexicon,
    parse_text,
    segment_document,
    split_sentences,
)


def test_split_sentences_respects_portuguese_abbreviations():
    text = (
        'O Sr. Silva chegou às 10h. Ele disse: "Olá!" Depois saiu... E voltou? Veja a pág. 3 agora.'
    )
    assert split_sentences(text) == [
        "O Sr. Silva chegou às 10h.",
        'Ele disse: "Olá!"',
        "Depois saiu...",
        "E voltou?",
        "Veja a pág. 3 agora.",
    ]


def test_split_sentences_keeps_initials_together():
    assert split_sentences("J. R. R. Tolkien escreveu. Fim.") == [
        "J. R. R. Tolkien escreveu.",
        "Fim.",
    ]


def test_clean_for_speech_handles_urls_emojis_and_spacing():
    cleaned = clean_for_speech("Veja https://www.exemplo.com.br/a/b 😀 agora  ,  ok")
    assert cleaned == "Veja exemplo.com.br agora, ok"


def test_parse_markdown_creates_sections_and_strips_syntax():
    doc = parse_text(
        "# Meu Livro\n\nIntro com **negrito** e [link](http://x.com).\n\n"
        "## Capítulo 1\n\nLinha um\ncontinua.\n\n- item\n- outro item\n\n```\ncodigo()\n```\n"
    )
    assert doc.title == "Meu Livro"
    titles = [s.title for s in doc.sections]
    assert titles == ["Meu Livro", "Capítulo 1"]
    assert doc.sections[0].paragraphs == ["Intro com negrito e link."]
    assert doc.sections[1].paragraphs == ["Linha um continua.", "item.", "outro item."]
    assert "codigo" not in doc.text
    assert doc.has_chapters


def test_parse_plain_text_detects_chapter_lines_but_not_sentences():
    doc = parse_text(
        "Capítulo 1\n\nTexto.\n\nParte final do livro foi escrita.\n\nCapítulo II - Fim\n\nMais."
    )
    assert [s.title for s in doc.sections] == ["Capítulo 1", "Capítulo II - Fim"]
    assert doc.sections[0].paragraphs == ["Texto.", "Parte final do livro foi escrita."]


def test_segment_document_respects_limits_and_pauses():
    long_paragraph = " ".join(["Esta é uma frase de teste com algumas palavras."] * 30)
    doc = parse_text(f"# Título\n\n{long_paragraph}\n\nSegundo parágrafo.")
    segments = segment_document(doc, max_chars=200)
    assert segments[0].is_title and segments[0].text == "Título"
    assert all(len(s.text) <= 200 for s in segments)
    assert segments[-1].pause == "end"
    pauses = [s.pause for s in segments]
    assert "paragraph" in pauses and "sentence" in pauses
    assert [s.index for s in segments] == list(range(len(segments)))


def test_segment_splits_giant_sentence():
    doc = parse_text("palavra, " * 200)
    segments = segment_document(doc, max_chars=120)
    assert len(segments) > 5
    assert all(len(s.text) <= 120 for s in segments)


def test_lexicon_parsing_and_application():
    lexicon = parse_lexicon("IA = i a\nTTS => tê tê ésse  # comentário\n\n# só comentário")
    assert lexicon == {"IA": "i a", "TTS": "tê tê ésse"}
    # all-caps terms are case-sensitive, so "ia" (verb) is preserved
    assert apply_lexicon("A IA usa TTS e ia longe.", lexicon) == "A i a usa tê tê ésse e ia longe."


def test_decode_bytes_falls_back_to_cp1252():
    assert decode_bytes("ação".encode("cp1252")) == "ação"
    assert decode_bytes("ação".encode()) == "ação"


def test_estimate_duration():
    assert estimate_duration("palavra " * 155) == pytest.approx(60)
    assert estimate_duration("palavra " * 155, rate=100) == pytest.approx(30)


def test_parse_html_uses_headings_and_skips_scripts():
    doc = parse_html(
        "<html><head><title>Página</title><script>var x=1</script></head><body>"
        "<nav>menu</nav><h1>Capítulo A</h1><p>Primeiro &amp; único.</p><ul><li>Um</li><li>Dois</li></ul>"
        "<h2>Capítulo B</h2><p>Fim</p></body></html>"
    )
    assert doc.title == "Página"
    assert [s.title for s in doc.sections] == ["Capítulo A", "Capítulo B"]
    assert doc.sections[0].paragraphs == ["Primeiro & único.", "Um.", "Dois."]
    assert "menu" not in doc.text and "var x" not in doc.text


def _make_docx(path):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = (
        f'<w:document xmlns:w="{w}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Introdução</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Olá </w:t></w:r><w:r><w:t>mundo.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", body)
    return path


def _make_epub(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<metadata><dc:title>Livro EPUB</dc:title></metadata>"
            '<manifest><item id="c1" href="c1.xhtml"/><item id="c2" href="c2.xhtml"/></manifest>'
            '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>',
        )
        zf.writestr("OEBPS/c1.xhtml", "<html><body><h1>Um</h1><p>Primeiro.</p></body></html>")
        zf.writestr("OEBPS/c2.xhtml", "<html><body><h1>Dois</h1><p>Segundo.</p></body></html>")
    return path


def _make_odt(path):
    content = (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
        "<text:h>Capítulo</text:h><text:p>Texto<text:s/>do ODT.</text:p>"
        "</office:text></office:body></office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("content.xml", content)
    return path


def test_load_docx_epub_odt(tmp_path):
    docx = load_document(_make_docx(tmp_path / "arquivo.docx"))
    assert docx.sections[0].title == "Introdução"
    assert docx.sections[0].paragraphs == ["Olá mundo."]

    epub = load_document(_make_epub(tmp_path / "livro.epub"))
    assert epub.title == "Livro EPUB"
    assert [s.title for s in epub.sections] == ["Um", "Dois"]

    odt = load_document(_make_odt(tmp_path / "doc.odt"))
    assert odt.sections[0].paragraphs == ["Texto do ODT."]


def test_load_txt_uses_filename_as_title(tmp_path):
    path = tmp_path / "minha_historia.txt"
    path.write_bytes("Era uma vez.".encode("cp1252"))
    doc = load_document(path)
    assert doc.title == "minha historia"
    assert doc.text == "Era uma vez."


def test_load_document_errors(tmp_path):
    with pytest.raises(TextExtractionError):
        load_document(tmp_path / "x.xyz")
    empty = tmp_path / "vazio.txt"
    empty.write_text("   \n\n")
    with pytest.raises(TextExtractionError):
        load_document(empty)
    broken = tmp_path / "quebrado.docx"
    broken.write_text("não é zip")
    with pytest.raises(TextExtractionError):
        load_document(broken)


def test_unspeakable_sentences_are_skipped_and_lists_keep_numbers():
    doc = parse_text(
        "Antes.\n\n...\n\n1. Primeiro passo\n2. Segundo passo\n\n- item solto\n\nArquivo nome_do_arquivo e *ênfase*."
    )
    texts = [s.text for s in segment_document(doc)]
    assert "..." not in texts
    assert "1. Primeiro passo." in texts
    assert "item solto." in texts
    assert "Arquivo nome do arquivo e ênfase." in texts
