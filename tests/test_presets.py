from texto_to_audio.presets import PRESETS, JobOptions


def test_from_dict_applies_preset_and_overrides():
    options = JobOptions.from_dict({"preset": "audiobook", "rate": 20})
    assert options.formats == ["m4b", "mp3"]
    assert options.loudness == -18.0
    assert options.sample_rate == 44_100
    assert options.rate == 20  # explicit value wins
    assert options.preset == "audiobook"


def test_none_never_overrides_preset_but_off_disables_loudness():
    assert JobOptions.from_dict({"preset": "video", "loudness": None}).loudness == -14.0
    assert JobOptions.from_dict({"preset": "video", "loudness": "off"}).loudness is None


def test_validation_clamps_and_filters():
    options = JobOptions.from_dict(
        {
            "rate": 500,
            "pitch": -40,
            "formats": "mp3, xyz, OPUS, mp3",
            "video": "cinema",
            "video_style": "?",
            "loudness": -99,
            "sample_rate": 22050,
            "unknown_field": 1,
            "lexicon": "não é dict",
            "max_chunk_chars": "abc",
        }
    )
    assert options.rate == 100
    assert options.pitch == -12
    assert options.formats == ["mp3", "opus"]
    assert options.video is None
    assert options.video_style == "waves"
    assert options.loudness == -36
    assert options.sample_rate == 48_000
    assert options.lexicon == {}
    assert options.max_chunk_chars == 80


def test_every_preset_is_valid():
    for name in PRESETS:
        options = JobOptions.from_dict({"preset": name})
        assert options.formats
        assert options.preset == name
