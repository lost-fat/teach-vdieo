"""ASR-to-canonical alignment contracts for the verification passage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.lesson_alignment import (
    LessonAlignmentError,
    align_asr_words,
    build_raw_qa_transcript,
)


ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = json.loads(
    (ROOT / "tests" / "fixtures" / "english_textbook_phase1.json").read_text(
        encoding="utf-8"
    )
)["source_text"]


def _raw_fixture() -> list[dict]:
    raw_tokens: list[str] = []
    kenya_occurrence = 0
    for token in SOURCE.split():
        if token == "Kenya's":
            kenya_occurrence += 1
            raw_tokens.extend(
                ["Kenya", "s"] if kenya_occurrence == 1 else ["Kenya's"]
            )
        elif token == "1901.":
            raw_tokens.extend(["nineteen", "oh", "one"])
        else:
            raw_tokens.append(token.strip(".,"))
    return [
        {
            "text": token,
            "begin_time_seconds": index * 0.25,
            "end_time_seconds": (index + 1) * 0.25,
        }
        for index, token in enumerate(raw_tokens)
    ]


def test_aligns_duplicate_contractions_and_spoken_1901_to_canonical_words():
    aligned = align_asr_words(SOURCE, _raw_fixture())

    assert [word["text"] for word in aligned] == SOURCE.split()
    assert len(aligned) == 26
    kenya_words = [word for word in aligned if word["text"] == "Kenya's"]
    assert len(kenya_words) == 2
    assert kenya_words[0]["end_ms"] - kenya_words[0]["start_ms"] == 500
    assert aligned[-1]["text"] == "1901."
    assert aligned[-1]["end_ms"] - aligned[-1]["start_ms"] == 750


def test_alignment_rejects_reordered_or_hallucinated_asr_words():
    raw = _raw_fixture()
    raw[0]["text"] = "After"

    with pytest.raises(LessonAlignmentError, match="do not exhaustively align"):
        align_asr_words(SOURCE, raw)


def test_raw_qa_transcript_keeps_recognized_words_not_canonical_substitutions():
    transcript = build_raw_qa_transcript(_raw_fixture())
    words = [item["word"] for item in transcript["word_timestamps"]]

    assert words[-3:] == ["nineteen", "oh", "one"]
    assert "1901." not in words
    assert transcript["source"] == "dashscope_asr_raw"


def test_alignment_rejects_large_overlapping_timestamps():
    raw = _raw_fixture()
    raw[1]["begin_time_seconds"] = 0.1

    with pytest.raises(LessonAlignmentError, match="overlaps"):
        align_asr_words(SOURCE, raw)
