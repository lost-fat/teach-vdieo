"""Align raw ASR timing to immutable English textbook source words."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Mapping, Sequence


class LessonAlignmentError(ValueError):
    """Raised when raw ASR cannot be mapped to the canonical source."""


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalized_token(value: object) -> str:
    text = str(value).casefold().replace("’", "'")
    return _NON_ALNUM.sub("", text)


def _canonical_aliases(token: str) -> set[str]:
    normalized = _normalized_token(token)
    aliases = {normalized}
    # File-trans ASR commonly renders a year as spoken words.  Keep this
    # explicit rather than replacing the canonical caption text.
    if normalized == "1901":
        aliases.update(
            {
                "nineteenohone",
                "nineteenoone",
                "nineteenzeroone",
                "nineteenhundredone",
                "nineteenhundredandone",
            }
        )
    return aliases


def _raw_word(word: Mapping[str, Any], position: int) -> dict[str, Any]:
    text = word.get("text", word.get("word", ""))
    normalized = _normalized_token(text)
    if not normalized:
        raise LessonAlignmentError(
            f"ASR word {position} contains no comparable letters or digits"
        )

    start = word.get("begin_time_seconds", word.get("start_seconds"))
    end = word.get("end_time_seconds", word.get("end_seconds"))
    if isinstance(start, bool) or isinstance(end, bool):
        raise LessonAlignmentError(f"ASR word {position} has invalid timing")
    try:
        start_ms = round(float(start) * 1000)
        end_ms = round(float(end) * 1000)
    except (TypeError, ValueError) as exc:
        raise LessonAlignmentError(
            f"ASR word {position} has invalid timing"
        ) from exc
    if start_ms < 0 or end_ms <= start_ms:
        raise LessonAlignmentError(
            f"ASR word {position} timing must be positive and ordered"
        )
    return {
        "text": str(text),
        "normalized": normalized,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def align_asr_words(
    source_text: str,
    asr_words: Sequence[Mapping[str, Any]],
    *,
    max_asr_words_per_source_word: int = 5,
) -> list[dict[str, Any]]:
    """Return canonical source words with timing derived from raw ASR.

    Alignment is ordered and exhaustive.  It supports contractions split by
    ASR (``Kenya`` + ``s`` → ``Kenya's``) and the spoken-year variants used by
    this Phase 1 fixture.  It never writes an ASR guess into caption text.
    """

    if not isinstance(source_text, str) or not source_text.strip():
        raise LessonAlignmentError("source_text must be non-empty")
    if not isinstance(asr_words, Sequence) or isinstance(asr_words, (str, bytes)):
        raise LessonAlignmentError("asr_words must be a sequence")

    canonical = source_text.split()
    raw = [_raw_word(word, index) for index, word in enumerate(asr_words)]
    if not raw:
        raise LessonAlignmentError("ASR returned no words")

    previous_end = 0
    for position, word in enumerate(raw):
        if word["start_ms"] < previous_end:
            overlap = previous_end - word["start_ms"]
            if overlap > 50:
                raise LessonAlignmentError(
                    f"ASR word {position} overlaps its predecessor by {overlap} ms"
                )
            word["start_ms"] = previous_end
            if word["end_ms"] <= word["start_ms"]:
                raise LessonAlignmentError(
                    f"ASR word {position} collapses after overlap correction"
                )
        previous_end = word["end_ms"]

    @lru_cache(maxsize=None)
    def solve(source_index: int, asr_index: int) -> tuple[tuple[int, int], ...] | None:
        if source_index == len(canonical) and asr_index == len(raw):
            return ()
        if source_index >= len(canonical) or asr_index >= len(raw):
            return None

        aliases = _canonical_aliases(canonical[source_index])
        upper = min(
            len(raw), asr_index + max(1, max_asr_words_per_source_word)
        )
        combined = ""
        for end_index in range(asr_index, upper):
            combined += raw[end_index]["normalized"]
            if combined not in aliases:
                continue
            tail = solve(source_index + 1, end_index + 1)
            if tail is not None:
                return ((asr_index, end_index + 1),) + tail
        return None

    mapping = solve(0, 0)
    if mapping is None:
        raw_text = " ".join(word["text"] for word in raw)
        raise LessonAlignmentError(
            "ASR words do not exhaustively align to the locked source "
            f"(source_words={len(canonical)}, asr_words={len(raw)}; "
            f"raw={raw_text!r})"
        )

    return [
        {
            "text": canonical[index],
            "start_ms": raw[start]["start_ms"],
            "end_ms": raw[end - 1]["end_ms"],
        }
        for index, (start, end) in enumerate(mapping)
    ]


def build_raw_qa_transcript(
    asr_words: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build final-review input that preserves ASR-recognized words verbatim."""

    normalized = [_raw_word(word, index) for index, word in enumerate(asr_words)]
    return {
        "version": "1.0",
        "source": "dashscope_asr_raw",
        "word_timestamps": [
            {
                "word": word["text"],
                "start": word["start_ms"] / 1000,
                "end": word["end_ms"] / 1000,
            }
            for word in normalized
        ],
    }
