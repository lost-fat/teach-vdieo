"""Deterministic source-lock contracts for English textbook pipelines.

This module deliberately contains no file, network, or model interactions.  It
normalizes user-provided text into a canonical narration source and verifies
that a narration timeline covers that source exactly once, in source order.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any


class LessonContractError(ValueError):
    """Raised when a lesson artifact violates a source-fidelity contract."""


_TYPOGRAPHIC_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def normalize_source_text(source_text: str) -> tuple[str, list[str]]:
    """Return canonical source text and an ordered log of applied changes.

    Normalization is intentionally conservative: it standardizes line endings,
    typographic quotes, whitespace runs, and outer whitespace.  It never tries
    to repair spelling, punctuation, or missing spaces between words.
    """

    if not isinstance(source_text, str):
        raise LessonContractError("source text must be a string")

    text = source_text
    changes: list[str] = []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized != text:
        changes.append("normalize_line_endings")
        text = normalized

    normalized = text.translate(_TYPOGRAPHIC_QUOTES)
    if normalized != text:
        changes.append("normalize_typographic_quotes")
        text = normalized

    normalized = re.sub(r"\s+", " ", text)
    if normalized != text:
        changes.append("collapse_whitespace")
        text = normalized

    normalized = text.strip()
    if normalized != text:
        changes.append("trim_outer_whitespace")
        text = normalized

    return text, changes


def build_lesson_source(source_text: str, *, language: str = "en") -> dict[str, Any]:
    """Build a deterministic, verbatim lesson-source artifact."""

    if not isinstance(language, str) or not language.strip():
        raise LessonContractError("language must be a non-empty string")

    normalized_text, changes = normalize_source_text(source_text)
    if not normalized_text:
        raise LessonContractError("source text is empty after normalization")

    return {
        "version": "1.0",
        "language": language,
        "source_text": source_text,
        "normalized_text": normalized_text,
        "source_sha256": _sha256(normalized_text),
        "adaptation_mode": "verbatim",
        "normalizations_applied": changes,
    }


def validate_narration_timeline(
    source_text: str, timeline: Mapping[str, Any]
) -> None:
    """Validate exact, ordered, contiguous coverage of ``source_text``.

    Character offsets are half-open ranges: ``source_start_char`` is included
    and ``source_end_char`` is excluded.  Units must appear in source order and
    their stored text must equal the corresponding source slice verbatim.
    """

    if not isinstance(source_text, str) or not source_text:
        raise LessonContractError("source text is empty")
    if not isinstance(timeline, Mapping):
        raise LessonContractError("narration timeline must be an object")

    expected_hash = _sha256(source_text)
    if timeline.get("source_sha256") != expected_hash:
        raise LessonContractError("narration timeline source hash does not match")

    units = timeline.get("units")
    if not isinstance(units, list) or not units:
        raise LessonContractError("narration timeline units must be a non-empty list")

    total_duration_ms = timeline.get("total_duration_ms")
    if not _is_character_offset(total_duration_ms) or total_duration_ms <= 0:
        raise LessonContractError("narration timeline total_duration_ms must be positive")

    next_start = 0
    previous_word_end = 0
    previous_beat_end = 0
    actual_duration_sum = 0
    for position, unit in enumerate(units):
        if not isinstance(unit, Mapping):
            raise LessonContractError(f"narration unit {position} must be an object")

        start = unit.get("source_start_char")
        end = unit.get("source_end_char")
        if not _is_character_offset(start) or not _is_character_offset(end):
            raise LessonContractError(
                f"narration unit {position} source character offsets must be integers"
            )
        if start < 0 or end <= start or end > len(source_text):
            raise LessonContractError(
                f"narration unit {position} source character range is out of bounds"
            )

        if start > next_start:
            raise LessonContractError(
                f"narration source coverage has a gap before unit {position}"
            )
        if start < next_start:
            raise LessonContractError(
                f"narration source coverage overlaps before unit {position}"
            )

        expected_unit_text = source_text[start:end]
        if unit.get("source_text") != expected_unit_text:
            raise LessonContractError(
                f"narration unit {position} source_text does not match its source range"
            )

        actual_duration_ms = unit.get("actual_duration_ms")
        if not _is_character_offset(actual_duration_ms) or actual_duration_ms <= 0:
            raise LessonContractError(
                f"narration unit {position} actual_duration_ms must be positive"
            )
        actual_duration_sum += actual_duration_ms

        words = unit.get("words")
        if not isinstance(words, list) or not words:
            raise LessonContractError(
                f"narration unit {position} word coverage must be non-empty"
            )
        expected_words = expected_unit_text.split()
        actual_words = [
            word.get("text") if isinstance(word, Mapping) else None
            for word in words
        ]
        if actual_words != expected_words:
            raise LessonContractError(
                f"narration unit {position} word coverage does not match source text"
            )
        for word_position, word in enumerate(words):
            start_ms = word.get("start_ms")
            end_ms = word.get("end_ms")
            if (
                not _is_character_offset(start_ms)
                or not _is_character_offset(end_ms)
                or start_ms < previous_word_end
                or start_ms >= end_ms
                or end_ms > total_duration_ms
            ):
                raise LessonContractError(
                    "narration word timing must be ordered, non-overlapping, "
                    f"positive, and within total_duration_ms (unit {position}, "
                    f"word {word_position})"
                )
            previous_word_end = end_ms

        visual_beats = unit.get("visual_beats")
        if not isinstance(visual_beats, list) or not visual_beats:
            raise LessonContractError(
                f"narration unit {position} visual beats must be non-empty"
            )
        for beat_position, beat in enumerate(visual_beats):
            if not isinstance(beat, Mapping):
                raise LessonContractError(
                    f"narration unit {position} visual beat {beat_position} "
                    "must be an object"
                )
            start_ms = beat.get("start_ms")
            end_ms = beat.get("end_ms")
            if (
                not _is_character_offset(start_ms)
                or not _is_character_offset(end_ms)
                or start_ms < previous_beat_end
                or start_ms >= end_ms
                or end_ms > total_duration_ms
            ):
                raise LessonContractError(
                    "narration visual beat timing must be ordered, non-overlapping, "
                    f"positive, and within total_duration_ms (unit {position}, "
                    f"beat {beat_position})"
                )
            previous_beat_end = end_ms

        next_start = end

    if next_start != len(source_text):
        raise LessonContractError("narration source coverage has a gap at the end")
    if actual_duration_sum > total_duration_ms:
        raise LessonContractError(
            "narration actual durations exceed total_duration_ms"
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_character_offset(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
