from texto_to_audio.subtitles import (
    ChunkTiming,
    anchors_from_boundaries,
    build_cues,
    to_srt,
    to_vtt,
    transcript,
    wrap_cue,
)


def test_cues_are_proportional_and_ordered():
    timings = [
        ChunkTiming(start=0.0, speech=4.0, sentences=["Curta.", "Uma frase bem mais longa aqui."]),
        ChunkTiming(start=5.0, speech=2.0, sentences=["Fim."]),
    ]
    cues = build_cues(timings)
    assert [c.text for c in cues] == ["Curta.", "Uma frase bem mais longa aqui.", "Fim."]
    assert cues[0].start == 0.0
    assert cues[1].start > cues[0].start
    assert cues[2].start == 5.0
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start


def test_long_sentence_split_into_multiple_cues_with_two_lines():
    sentence = "palavra " * 40
    cues = build_cues([ChunkTiming(start=0, speech=20, sentences=[sentence.strip()])], max_chars=84)
    assert len(cues) >= 3
    assert all(len(line) <= 42 for c in cues for line in c.text.split("\n"))
    assert all(c.text.count("\n") <= 1 for c in cues)


def test_engine_boundaries_become_anchors():
    anchors = anchors_from_boundaries([(0.2, 1.2, "a"), (1.4, 3.2, "b")], expected=2)
    assert anchors[0][0] == 0.0
    assert anchors[-1][1] == 1.0
    assert anchors_from_boundaries([(0, 1, "a")], expected=2) is None


def test_srt_and_vtt_format():
    cues = build_cues([ChunkTiming(start=3661.5, speech=2.0, sentences=["Olá."])])
    srt = to_srt(cues)
    assert srt.startswith("1\n01:01:01,500 --> ")
    vtt = to_vtt(cues)
    assert vtt.startswith("WEBVTT\n")
    assert "01:01:01.500 --> " in vtt


def test_wrap_cue_balances_lines():
    text = "Esta legenda tem um tamanho médio para duas linhas"
    wrapped = wrap_cue(text)
    first, second = wrapped.split("\n")
    assert abs(len(first) - len(second)) < 15


def test_transcript_marks_titles():
    items = transcript([ChunkTiming(start=0, speech=1, sentences=["Capítulo 1"], is_title=True)])
    assert items == [{"start": 0.0, "end": 1.0, "text": "Capítulo 1", "title": True}]
