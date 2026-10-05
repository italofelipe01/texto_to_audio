"""Text ingestion: file extraction, cleanup for speech, sentence splitting and chunking.

Every supported format is converted into a :class:`Document` (sections made of
paragraphs).  Headings become sections so they can later be turned into audio
chapters.  Only ``pypdf`` is optional; DOCX, ODT and EPUB are parsed with the
standard library.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_EXTENSIONS = (
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".xhtml",
    ".docx",
    ".odt",
    ".epub",
    ".pdf",
)

WORDS_PER_MINUTE = 155


class TextExtractionError(ValueError):
    """Raised when a file cannot be read or contains no usable text."""


@dataclass
class Section:
    title: str | None
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Document:
    title: str | None
    sections: list[Section]

    @property
    def text(self) -> str:
        parts: list[str] = []
        for section in self.sections:
            if section.title:
                parts.append(section.title)
            parts.extend(section.paragraphs)
        return "\n\n".join(parts)

    @property
    def has_chapters(self) -> bool:
        return sum(1 for s in self.sections if s.title) >= 2

    def is_empty(self) -> bool:
        return not any(s.paragraphs or s.title for s in self.sections)


@dataclass
class Segment:
    """A piece of text synthesized in a single TTS request."""

    index: int
    text: str
    sentences: list[str]
    section: int
    section_title: str | None
    pause: str  # "sentence" | "paragraph" | "section" | "end"
    is_title: bool = False


# --------------------------------------------------------------------------- #
# Encoding / cleanup helpers
# --------------------------------------------------------------------------- #


def decode_bytes(data: bytes) -> str:
    """Decode text trying the encodings most common for Portuguese files."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\U0000fe0f\U0000200d]+"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _url_to_speech(match: re.Match[str]) -> str:
    url = re.sub(r"^(?:https?://)?(?:www\.)?", "", match.group(0), flags=re.IGNORECASE)
    return url.split("/")[0].rstrip(".,;:")


def clean_for_speech(text: str, *, strip_emojis: bool = True) -> str:
    """Normalize a paragraph so TTS engines read it naturally."""
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _URL_RE.sub(_url_to_speech, text)
    if strip_emojis:
        text = _EMOJI_RE.sub(" ", text)
    text = text.replace(" ", " ").replace("­", "")
    text = re.sub(r"[•▪●◦■□►▶➔→]+", " ", text)
    text = re.sub(r"(?<=\w)_(?=\w)", " ", text)  # snake_case → words
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,;:!?…])", r"\1", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Pronunciation lexicon
# --------------------------------------------------------------------------- #


def parse_lexicon(raw: str) -> dict[str, str]:
    """Parse ``termo = pronúncia`` lines (``#`` starts a comment)."""
    lexicon: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for sep in ("=>", "=", "\t"):
            if sep in line:
                term, spoken = line.split(sep, 1)
                if term.strip():
                    lexicon[term.strip()] = spoken.strip()
                break
    return lexicon


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    """Replace whole-word occurrences; all-caps terms (acronyms) are case-sensitive."""
    for term in sorted(lexicon, key=len, reverse=True):
        flags = 0 if term.isupper() else re.IGNORECASE
        pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
        text = re.sub(pattern, lambda _m, s=lexicon[term]: s, text, flags=flags)
    return text


# --------------------------------------------------------------------------- #
# Sentence splitting and chunking
# --------------------------------------------------------------------------- #

ABBREVIATIONS = {
    "sr",
    "sra",
    "srta",
    "dr",
    "dra",
    "prof",
    "profa",
    "exmo",
    "exma",
    "ilmo",
    "ilma",
    "sto",
    "sta",
    "av",
    "r",
    "pç",
    "tel",
    "cel",
    "p",
    "pp",
    "pág",
    "pag",
    "págs",
    "n",
    "nº",
    "no",
    "núm",
    "num",
    "art",
    "arts",
    "inc",
    "cap",
    "caps",
    "vol",
    "vols",
    "ed",
    "aprox",
    "fig",
    "figs",
    "obs",
    "ex",
    "jr",
    "ltda",
    "cia",
    "dept",
    "depto",
    "min",
    "máx",
    "max",
    "séc",
    "sec",
    "op",
    "cit",
    "vs",
    "mr",
    "mrs",
    "ms",
    "st",
    "eng",
    "gen",
    "gov",
    "adv",
    "arq",
    "trad",
    "org",
    "orgs",
    "coord",
    "et",
    "al",
}

_SPEAKABLE_RE = re.compile(r"\w")
_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"'”’»)\]]*(?=\s+)")
_WORD_BEFORE_RE = re.compile(r"(\S+?)[.!?…]+[\"'”’»)\]]*$")


def split_sentences(paragraph: str) -> list[str]:
    """Split a paragraph into sentences, aware of common Portuguese abbreviations."""
    paragraph = paragraph.strip()
    if not paragraph:
        return []
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(paragraph):
        end = match.end()
        candidate = paragraph[start:end]
        punct = match.group(0)
        if punct.startswith(".") and not punct.startswith(".."):
            word_match = _WORD_BEFORE_RE.search(candidate)
            word = word_match.group(1).lower().lstrip("(\"'“«") if word_match else ""
            if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
            if (
                word.isdigit()
                and len(word) <= 3
                and not sentences
                and start == 0
                and len(candidate) <= 4
            ):
                continue  # numbered list item: "1. Primeiro passo"
        rest = paragraph[end:].lstrip()
        if rest and rest[0].islower():
            continue
        sentences.append(candidate.strip())
        start = end
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Break a sentence longer than ``max_chars`` at punctuation, then at spaces."""
    if len(sentence) <= max_chars:
        return [sentence]
    pieces: list[str] = []
    for part in re.split(r"(?<=[,;:—–])\s+", sentence):
        if len(part) <= max_chars:
            pieces.append(part)
            continue
        words = part.split()
        current = ""
        for word in words:
            if current and len(current) + 1 + len(word) > max_chars:
                pieces.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            pieces.append(current)
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + 1 + len(piece) <= max_chars:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def segment_document(
    doc: Document, max_chars: int = 400, *, speak_titles: bool = True
) -> list[Segment]:
    """Turn a document into TTS-sized segments that never cross paragraph boundaries."""
    max_chars = max(80, max_chars)
    segments: list[Segment] = []

    def add(
        text: str, sentences: list[str], sec: int, title: str | None, pause: str, is_title=False
    ):
        segments.append(
            Segment(len(segments), text, sentences, sec, title, pause, is_title=is_title)
        )

    for sec_index, section in enumerate(doc.sections):
        if section.title and speak_titles:
            add(section.title, [section.title], sec_index, section.title, "paragraph", True)
        for paragraph in section.paragraphs:
            sentences: list[str] = []
            for sentence in split_sentences(paragraph):
                if not _SPEAKABLE_RE.search(sentence):
                    continue  # e.g. "..." or "***": engines return no audio for these
                sentences.extend(_split_long(sentence, max_chars))
            current: list[str] = []
            for sentence in sentences:
                if current and len(" ".join(current)) + 1 + len(sentence) > max_chars:
                    add(" ".join(current), current, sec_index, section.title, "sentence")
                    current = []
                current.append(sentence)
            if current:
                add(" ".join(current), current, sec_index, section.title, "paragraph")
        if segments and segments[-1].section == sec_index:
            segments[-1].pause = "section"
    if segments:
        segments[-1].pause = "end"
    return segments


def estimate_duration(text: str, rate: int = 0) -> float:
    """Rough speech duration in seconds (155 words/min at normal speed)."""
    words = len(text.split())
    speed = max(0.3, 1 + rate / 100)
    return words / WORDS_PER_MINUTE * 60 / speed


# --------------------------------------------------------------------------- #
# Plain text / Markdown
# --------------------------------------------------------------------------- #

_MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_CHAPTER_RE = re.compile(
    r"^\s*(?:(?:cap[íi]tulo|parte|livro|se[çc][ãa]o|chapter|part)\s+(?:\d+|[ivxlcdm]+|[a-zà-ú]+)\b"
    r"|(?:pr[óo]logo|ep[íi]logo|introdu[çc][ãa]o|conclus[ãa]o|pref[áa]cio|ap[êe]ndice)\s*(?:$|[:\-–—]))"
    r"[^.!?;,]{0,70}$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_BULLET_RE = re.compile(r"^\s*[-*+•]\s+")  # numbered items keep their number


def _strip_markdown_inline(line: str) -> str:
    line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"(?<![\w*~])(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1(?![\w*~])", r"\2", line)
    if line.lstrip().startswith("|"):
        line = ", ".join(c.strip() for c in line.strip().strip("|").split("|") if c.strip())
        if re.fullmatch(r"[\s,:\-]*", line):
            return ""
    return line


def _ensure_terminal_punct(text: str) -> str:
    text = text.rstrip()
    if text and text[-1] not in ".!?…:;,\"'”’»)":
        text += "."
    return text


def parse_text(text: str, *, title: str | None = None, markdown: bool = True) -> Document:
    """Parse plain text (optionally Markdown) into sections and paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if markdown:
        text = re.sub(r"```.*?```", "\n", text, flags=re.DOTALL)
    sections: list[Section] = [Section(title=None)]
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraph = clean_for_speech(" ".join(buffer))
            if paragraph:
                sections[-1].paragraphs.append(paragraph)
            buffer.clear()

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or (markdown and re.fullmatch(r"[-*_=]{3,}", line)):
            flush()
            continue
        heading = _MD_HEADING_RE.match(line) if markdown else None
        if heading or (len(line) <= 80 and not buffer and _CHAPTER_RE.match(line)):
            flush()
            heading_text = heading.group(2) if heading else line
            heading_text = clean_for_speech(_strip_markdown_inline(heading_text))
            if (
                heading
                and len(heading.group(1)) == 1
                and title is None
                and not any(s.paragraphs or s.title for s in sections)
            ):
                title = heading_text
            sections.append(Section(title=heading_text))
            continue
        if markdown:
            line = re.sub(r"^\s*>\s?", "", line)
            line = _strip_markdown_inline(line)
            if not line.strip():
                continue
        if _LIST_RE.match(line):
            flush()
            buffer.append(_ensure_terminal_punct(_BULLET_RE.sub("", line)))
            flush()
            continue
        buffer.append(line)
    flush()

    sections = [s for s in sections if s.paragraphs or s.title]
    doc = Document(title=title, sections=sections or [Section(None)])
    if doc.title is None:
        doc.title = next((s.title for s in doc.sections if s.title), None)
    return doc


# --------------------------------------------------------------------------- #
# HTML / EPUB
# --------------------------------------------------------------------------- #

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "section",
    "article",
    "blockquote",
    "pre",
    "dd",
    "dt",
    "figcaption",
    "caption",
    "td",
    "th",
    "hr",
    "ul",
    "ol",
    "table",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style", "head", "nav", "noscript", "svg", "template", "aside", "footer"}


class _HTMLToDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = [Section(None)]
        self.title: str | None = None
        self._buffer: list[str] = []
        self._skip = 0
        self._in_heading: str | None = None
        self._in_title = False
        self._heading_buffer: list[str] = []

    def _flush(self) -> None:
        paragraph = clean_for_speech(" ".join(self._buffer))
        if paragraph:
            self.sections[-1].paragraphs.append(paragraph)
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _HEADING_TAGS:
            self._flush()
            self._in_heading = tag
            self._heading_buffer = []
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _HEADING_TAGS and self._in_heading == tag:
            heading = clean_for_speech(" ".join(self._heading_buffer))
            self._in_heading = None
            if heading:
                self.sections.append(Section(title=heading))
        elif tag in _BLOCK_TAGS:
            if tag == "li" and self._buffer:
                self._buffer[-1] = _ensure_terminal_punct(self._buffer[-1])
            self._flush()

    def handle_data(self, data):
        if self._in_title and self.title is None and data.strip():
            self.title = clean_for_speech(data)
            return
        if self._skip:
            return
        if self._in_heading:
            self._heading_buffer.append(data)
        else:
            self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()


def parse_html(html: str) -> Document:
    parser = _HTMLToDocument()
    parser.feed(html)
    parser.close()
    sections = [s for s in parser.sections if s.paragraphs or s.title]
    title = parser.title or next((s.title for s in sections if s.title), None)
    return Document(title=title, sections=sections or [Section(None)])


def _read_epub(path: Path) -> Document:
    with zipfile.ZipFile(path) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = next(el for el in container.iter() if el.tag.endswith("rootfile"))
        opf_path = rootfile.attrib["full-path"]
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        manifest = {
            item.attrib["id"]: item.attrib["href"]
            for item in opf.iter()
            if item.tag.endswith("item") and "id" in item.attrib
        }
        title_el = next((el for el in opf.iter() if el.tag.endswith("title")), None)
        title = clean_for_speech(title_el.text) if title_el is not None and title_el.text else None
        sections: list[Section] = []
        for itemref in (el for el in opf.iter() if el.tag.endswith("itemref")):
            href = manifest.get(itemref.attrib.get("idref", ""))
            if not href:
                continue
            name = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
            try:
                html = decode_bytes(zf.read(name))
            except KeyError:
                continue
            chapter = parse_html(html)
            for section in chapter.sections:
                if section.paragraphs or section.title:
                    sections.append(section)
    return Document(title=title, sections=sections or [Section(None)])


# --------------------------------------------------------------------------- #
# Office documents (DOCX / ODT) and PDF
# --------------------------------------------------------------------------- #

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_docx(path: Path) -> Document:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
        title = None
        if "docProps/core.xml" in zf.namelist():
            core = ET.fromstring(zf.read("docProps/core.xml"))
            title_el = next((el for el in core.iter() if el.tag.endswith("}title")), None)
            if title_el is not None and title_el.text:
                title = clean_for_speech(title_el.text)
    sections = [Section(None)]
    for para in root.iter(f"{_W}p"):
        style_el = para.find(f"{_W}pPr/{_W}pStyle")
        style = (style_el.attrib.get(f"{_W}val", "") if style_el is not None else "").lower()
        parts: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{_W}tab", f"{_W}br"):
                parts.append(" ")
        text = clean_for_speech("".join(parts))
        if not text:
            continue
        if style.startswith(("heading", "ttulo", "titulo", "título")) or style == "title":
            if style == "title" and title is None:
                title = text
            sections.append(Section(title=text))
        else:
            sections[-1].paragraphs.append(text)
    sections = [s for s in sections if s.paragraphs or s.title]
    title = title or next((s.title for s in sections if s.title), None)
    return Document(title=title, sections=sections or [Section(None)])


_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def _odt_text(node: ET.Element) -> str:
    parts = [node.text or ""]
    for child in node:
        if child.tag in (f"{_ODT_TEXT}s", f"{_ODT_TEXT}tab", f"{_ODT_TEXT}line-break"):
            parts.append(" ")
        else:
            parts.append(_odt_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _read_odt(path: Path) -> Document:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("content.xml"))
    sections = [Section(None)]
    for node in root.iter():
        if node.tag == f"{_ODT_TEXT}h":
            heading = clean_for_speech(_odt_text(node))
            if heading:
                sections.append(Section(title=heading))
        elif node.tag == f"{_ODT_TEXT}p":
            text = clean_for_speech(_odt_text(node))
            if text:
                sections[-1].paragraphs.append(text)
    sections = [s for s in sections if s.paragraphs or s.title]
    title = next((s.title for s in sections if s.title), None)
    return Document(title=title, sections=sections or [Section(None)])


def _read_pdf(path: Path) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise TextExtractionError(
            "Leitura de PDF requer o pacote opcional 'pypdf' (pip install texto-to-audio[pdf])."
        ) from exc
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises many different error types
        raise TextExtractionError(f"PDF inválido ou corrompido ({path.name}): {exc}") from exc
    text = "\n\n".join(pages)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # hyphenated line breaks
    text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)  # page numbers
    title = None
    if reader.metadata and reader.metadata.title:
        title = clean_for_speech(str(reader.metadata.title)) or None
    if not text.strip():
        raise TextExtractionError(
            "O PDF não contém texto extraível (provavelmente é digitalizado). "
            "Use OCR antes (ex.: ocrmypdf) e envie novamente."
        )
    return parse_text(text, title=title, markdown=False)


def load_document(path: str | Path) -> Document:
    """Read any supported file and return a :class:`Document`."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise TextExtractionError(
            f"Formato '{suffix or path.name}' não suportado. Use: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    try:
        if suffix in (".txt",) or suffix in (".md", ".markdown"):
            doc = parse_text(decode_bytes(path.read_bytes()), markdown=True)
        elif suffix in (".html", ".htm", ".xhtml"):
            doc = parse_html(decode_bytes(path.read_bytes()))
        elif suffix == ".docx":
            doc = _read_docx(path)
        elif suffix == ".odt":
            doc = _read_odt(path)
        elif suffix == ".epub":
            doc = _read_epub(path)
        else:
            doc = _read_pdf(path)
    except TextExtractionError:
        raise
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, StopIteration) as exc:
        raise TextExtractionError(f"Não foi possível ler '{path.name}': {exc}") from exc
    if doc.is_empty():
        raise TextExtractionError(f"Nenhum texto encontrado em '{path.name}'.")
    if not doc.title:
        doc.title = path.stem.replace("_", " ").replace("-", " ").strip() or None
    return doc
