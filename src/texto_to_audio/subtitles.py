"""Subtitle (SRT/WebVTT) and transcript generation from the audio timeline."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field


@dataclass
class Cue:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class ChunkTiming:
    """Where a synthesized chunk landed in the final audio."""

    start: float
    speech: float
    sentences: list[str]
    # (start, end) fractions of the speech duration for each sentence, when known
    anchors: list[tuple[float, float]] | None = None
    section_title: str | None = None
    is_title: bool = False
    extra: dict = field(default_factory=dict)


def anchors_from_boundaries(
    boundaries: list[tuple[float, float, str]], expected: int
) -> list[tuple[float, float]] | None:
    """Convert engine sentence boundaries (seconds) into fractions of the trimmed speech."""
    if not boundaries or len(boundaries) != expected:
        return None
    first = boundaries[0][0]
    last = max(end for _s, end, _t in boundaries)
    span = last - first
    if span <= 0:
        return None
    return [(max(0.0, (s - first) / span), min(1.0, (e - first) / span)) for s, e, _t in boundaries]


def _proportional(sentences: list[str]) -> list[tuple[float, float]]:
    weights = [max(1, len(s)) for s in sentences]
    total = float(sum(weights))
    spans, acc = [], 0.0
    for w in weights:
        spans.append((acc / total, (acc + w) / total))
        acc += w
    return spans


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for token in re.split(r"(?<=[,;:—–])\s+|\s+", text):
        if not token:
            continue
        candidate = f"{current} {token}".strip()
        if current and len(candidate) > max_chars:
            parts.append(current)
            current = token
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def wrap_cue(text: str, line_len: int = 42) -> str:
    """Wrap into at most two balanced lines."""
    if len(text) <= line_len:
        return text
    width = max(line_len // 2, (len(text) + 1) // 2 + 4)
    lines = textwrap.wrap(text, width=min(width, line_len))
    if len(lines) > 2:
        lines = textwrap.wrap(text, width=line_len)
    return "\n".join(lines)


def build_cues(
    timings: list[ChunkTiming],
    *,
    max_chars: int = 84,
    line_len: int = 42,
    min_duration: float = 0.7,
) -> list[Cue]:
    cues: list[Cue] = []
    for timing in timings:
        if not timing.sentences or timing.speech <= 0:
            continue
        anchors = timing.anchors
        if not anchors or len(anchors) != len(timing.sentences):
            anchors = _proportional(timing.sentences)
        for sentence, (fa, fb) in zip(timing.sentences, anchors, strict=False):
            s_start = timing.start + fa * timing.speech
            s_end = timing.start + fb * timing.speech
            pieces = _split_text(sentence, max_chars)
            for piece, (pa, pb) in zip(pieces, _proportional(pieces), strict=False):
                start = s_start + pa * (s_end - s_start)
                end = s_start + pb * (s_end - s_start)
                cues.append(Cue(start, end, wrap_cue(piece, line_len)))
    # enforce ordering, minimum duration and no overlaps
    for i, cue in enumerate(cues):
        next_start = cues[i + 1].start if i + 1 < len(cues) else None
        if cue.end - cue.start < min_duration:
            cue.end = cue.start + min_duration
        if next_start is not None and cue.end > next_start:
            cue.end = max(cue.start + 0.05, next_start - 0.01)
    return cues


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{i}\n{_ts(c.start, ',')} --> {_ts(c.end, ',')}\n{c.text}\n" for i, c in enumerate(cues, 1)
    ]
    return "\n".join(blocks)


def to_vtt(cues: list[Cue]) -> str:
    blocks = ["WEBVTT\n"]
    blocks += [f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n{c.text}\n" for c in cues]
    return "\n".join(blocks)


def transcript(timings: list[ChunkTiming]) -> list[dict]:
    """Sentence-level transcript used by the web player to highlight the text."""
    items: list[dict] = []
    for timing in timings:
        anchors = timing.anchors
        if not anchors or len(anchors) != len(timing.sentences):
            anchors = _proportional(timing.sentences)
        for sentence, (fa, fb) in zip(timing.sentences, anchors, strict=False):
            items.append(
                {
                    "start": round(timing.start + fa * timing.speech, 3),
                    "end": round(timing.start + fb * timing.speech, 3),
                    "text": sentence,
                    "title": timing.is_title,
                }
            )
    return items
