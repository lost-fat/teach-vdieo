"""Behavioral contracts for article-scale semantic subtitle planning."""

from __future__ import annotations

import json

from tools.subtitle.subtitle_gen import SubtitleGen


MOMBASA_WORDS = [
    ("Before", 0.088, 0.476),
    ("then,", 0.476, 0.786),
    ("the", 0.942, 1.097),
    ("only", 1.097, 1.408),
    ("transport", 1.408, 1.952),
    ("links", 1.952, 2.262),
    ("between", 2.262, 2.650),
    ("Mombasa,", 2.650, 3.272),
    ("Kenya's", 3.340, 3.728),
    ("main", 3.728, 3.961),
    ("port,", 3.961, 4.427),
    ("and", 4.525, 4.679),
    ("Nairobi,", 4.679, 5.223),
    ("Kenya's", 5.331, 5.641),
    ("capital,", 5.719, 6.184),
    ("were", 6.427, 6.505),
    ("rough", 6.505, 6.738),
    ("roads", 6.816, 7.204),
    ("and", 7.204, 7.359),
    ("an", 7.359, 7.437),
    ("old", 7.437, 7.669),
    ("railway", 7.669, 8.059),
    ("line", 8.059, 8.369),
    ("completed", 8.369, 8.912),
    ("in", 8.912, 9.068),
    ("1901.", 9.068, 9.923),
]


def _segment(words: list[tuple[str, float, float]]) -> list[dict]:
    return [
        {
            "text": " ".join(word for word, _, _ in words),
            "start": words[0][1],
            "end": words[-1][2],
            "words": [
                {"word": word, "start": start, "end": end}
                for word, start, end in words
            ],
        }
    ]


def _semantic_json(tmp_path, words, **overrides):
    output = tmp_path / "captions.caption.json"
    inputs = {
        "segments": _segment(words),
        "format": "json",
        "output_path": str(output),
        "grouping_mode": "semantic",
        "max_chars_per_line": 42,
        "max_lines": 2,
        "max_words_per_cue": 14,
        "min_cue_duration_seconds": 1.5,
        "target_cue_duration_seconds": 3.0,
        "max_cue_duration_seconds": 4.5,
    }
    inputs.update(overrides)
    result = SubtitleGen().execute(inputs)
    assert result.success, result.error
    return json.loads(output.read_text(encoding="utf-8"))


def test_semantic_planner_uses_timing_and_boundary_hints_instead_of_fixed_words(
    tmp_path,
):
    payload = _semantic_json(
        tmp_path,
        MOMBASA_WORDS,
        semantic_break_after_word_indices=[5, 14, 25],
        protected_word_spans=[
            {"startWordIndex": 7, "endWordIndex": 11},
            {"startWordIndex": 12, "endWordIndex": 15},
        ],
    )

    cues = payload["cues"]
    assert [cue["text"] for cue in cues] == [
        "Before then, the only transport links",
        "between Mombasa, Kenya's main port, and Nairobi, Kenya's capital,",
        "were rough roads and an old railway line completed in 1901.",
    ]
    assert all(cue["end"] - cue["start"] >= 1.5 for cue in cues)
    assert all(len(cue["lines"]) <= 2 for cue in cues)
    assert all(len(line) <= 42 for cue in cues for line in cue["lines"])
    assert [(cue["startWordIndex"], cue["endWordIndex"]) for cue in cues] == [
        (0, 6),
        (6, 15),
        (15, 26),
    ]


def test_semantic_planner_merges_a_too_short_sentence_with_following_context(
    tmp_path,
):
    words = [
        ("Yes.", 0.0, 0.35),
        ("The", 0.55, 0.80),
        ("railway", 0.80, 1.25),
        ("connected", 1.25, 1.75),
        ("communities", 1.75, 2.30),
        ("across", 2.30, 2.62),
        ("the", 2.62, 2.80),
        ("region.", 2.80, 3.35),
    ]
    payload = _semantic_json(
        tmp_path,
        words,
        semantic_break_after_word_indices=[0, 7],
    )

    assert payload["cues"][0]["text"] != "Yes."
    assert "Yes. The railway" in payload["cues"][0]["text"]


def test_semantic_planner_splits_long_text_by_layout_without_losing_words(tmp_path):
    tokens = [
        "Students", "who", "travel", "through", "different", "regions",
        "often", "notice", "how", "language", "changes", "gradually,",
        "while", "local", "history", "continues", "to", "shape", "the",
        "stories", "people", "share", "with", "visitors.",
    ]
    words = [
        (token, index * 0.32, (index + 1) * 0.32)
        for index, token in enumerate(tokens)
    ]
    payload = _semantic_json(
        tmp_path,
        words,
        max_chars_per_line=30,
        semantic_break_after_word_indices=[5, 11, 17, 23],
    )

    cues = payload["cues"]
    assert 2 < len(cues) < len(tokens)
    assert [word["word"] for cue in cues for word in cue["words"]] == tokens
    assert all(len(line) <= 30 for cue in cues for line in cue["lines"])

