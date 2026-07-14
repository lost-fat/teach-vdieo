"""Deterministic source-lock and narration coverage contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lib.lesson_source import (
    LessonContractError,
    build_lesson_source,
    normalize_source_text,
    validate_narration_timeline,
)


ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "english_textbook_phase1.json").read_text(
        encoding="utf-8"
    )
)


def test_normalization_is_deterministic_and_word_preserving():
    raw = "  Before\r\nthen,   Kenya’s main port.  "

    normalized, changes = normalize_source_text(raw)

    assert normalized == "Before then, Kenya's main port."
    assert changes == [
        "normalize_line_endings",
        "normalize_typographic_quotes",
        "collapse_whitespace",
        "trim_outer_whitespace",
    ]


def test_normalization_does_not_silently_fix_missing_space():
    normalized, _ = normalize_source_text("Kenya'smain port")
    assert normalized == "Kenya'smain port"


def test_build_lesson_source_locks_corrected_user_fixture():
    artifact = build_lesson_source(FIXTURE["source_text"], language="en")

    assert artifact["adaptation_mode"] == "verbatim"
    assert artifact["source_text"] == FIXTURE["source_text"]
    assert artifact["normalized_text"] == FIXTURE["source_text"]
    assert artifact["source_sha256"] == hashlib.sha256(
        FIXTURE["source_text"].encode("utf-8")
    ).hexdigest()


def test_empty_source_is_rejected():
    with pytest.raises(LessonContractError, match="empty"):
        build_lesson_source("  \n\t ")


def _timeline_for(source: str) -> dict:
    split = source.index(" and an old railway")
    return {
        "version": "1.0",
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "total_duration_ms": 10_000,
        "units": [
            {
                "id": "nu-001",
                "source_text": source[:split],
                "source_start_char": 0,
                "source_end_char": split,
                "audio_asset_id": "narration-nu-001",
                "audio_path": "projects/test/assets/audio/narration.wav",
                "actual_duration_ms": 6_000,
                "words": [
                    {"text": "Before", "start_ms": 0, "end_ms": 350},
                    {"text": "then,", "start_ms": 360, "end_ms": 650},
                ],
                "visual_beats": [
                    {
                        "id": "vb-001",
                        "start_ms": 0,
                        "end_ms": 6_000,
                        "visual_intent": "Map the coastal-to-capital route.",
                    }
                ],
            },
            {
                "id": "nu-002",
                "source_text": source[split:],
                "source_start_char": split,
                "source_end_char": len(source),
                "audio_asset_id": "narration-nu-002",
                "audio_path": "projects/test/assets/audio/narration-2.wav",
                "actual_duration_ms": 4_000,
                "words": [
                    {"text": "and", "start_ms": 6_000, "end_ms": 6_250},
                    {"text": "an", "start_ms": 6_260, "end_ms": 6_400},
                ],
                "visual_beats": [
                    {
                        "id": "vb-002",
                        "start_ms": 6_000,
                        "end_ms": 10_000,
                        "visual_intent": "Reveal the old railway line.",
                    }
                ],
            },
        ],
    }


def test_narration_timeline_accepts_exact_contiguous_source_coverage():
    source = FIXTURE["source_text"]
    validate_narration_timeline(source, _timeline_for(source))


def test_narration_timeline_rejects_gap():
    source = FIXTURE["source_text"]
    timeline = _timeline_for(source)
    timeline["units"][1]["source_start_char"] += 1

    with pytest.raises(LessonContractError, match="gap|contiguous"):
        validate_narration_timeline(source, timeline)


def test_narration_timeline_rejects_overlap():
    source = FIXTURE["source_text"]
    timeline = _timeline_for(source)
    timeline["units"][1]["source_start_char"] -= 1

    with pytest.raises(LessonContractError, match="overlap|contiguous"):
        validate_narration_timeline(source, timeline)


def test_narration_timeline_rejects_rewritten_unit_text():
    source = FIXTURE["source_text"]
    timeline = _timeline_for(source)
    timeline["units"][0]["source_text"] = "Rewritten narration"

    with pytest.raises(LessonContractError, match="source_text"):
        validate_narration_timeline(source, timeline)


def test_narration_timeline_rejects_wrong_hash():
    source = FIXTURE["source_text"]
    timeline = _timeline_for(source)
    timeline["source_sha256"] = "0" * 64

    with pytest.raises(LessonContractError, match="hash"):
        validate_narration_timeline(source, timeline)
