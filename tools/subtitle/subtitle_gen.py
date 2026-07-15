"""Subtitle generation tool.

Converts word-level timestamps from the transcriber into SRT, VTT,
or caption JSON formats. Pure Python — no external dependencies beyond
the standard library.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)


class SubtitleGen(BaseTool):
    name = "subtitle_gen"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "subtitle"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = []  # pure Python
    install_instructions = "No external dependencies required."
    agent_skills = ["remotion-best-practices"]

    capabilities = [
        "generate_srt",
        "generate_vtt",
        "generate_caption_json",
        "plan_semantic_cues",
    ]

    input_schema = {
        "type": "object",
        "required": ["segments"],
        "properties": {
            "segments": {
                "type": "array",
                "description": "Transcript segments from transcriber (with words and timestamps)",
            },
            "format": {
                "type": "string",
                "enum": ["srt", "vtt", "json"],
                "default": "srt",
            },
            "output_path": {"type": "string"},
            "max_chars_per_line": {"type": "integer", "minimum": 1, "default": 42},
            "max_words_per_cue": {"type": "integer", "minimum": 1, "default": 8},
            "max_lines": {"type": "integer", "minimum": 1, "default": 2},
            "grouping_mode": {
                "type": "string",
                "enum": ["fixed", "semantic"],
                "default": "fixed",
            },
            "semantic_break_after_word_indices": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "description": (
                    "Optional global word indices after which a language model or "
                    "parser identified a grammar-safe page boundary."
                ),
            },
            "protected_word_spans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["startWordIndex", "endWordIndex"],
                    "properties": {
                        "startWordIndex": {"type": "integer", "minimum": 0},
                        "endWordIndex": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                },
                "description": (
                    "Half-open word ranges that must stay on one caption page, "
                    "for example names, appositives, dates, and phrasal verbs."
                ),
            },
            "min_cue_duration_seconds": {"type": "number", "minimum": 0.1, "default": 1.5},
            "target_cue_duration_seconds": {"type": "number", "minimum": 0.1, "default": 3.0},
            "max_cue_duration_seconds": {"type": "number", "minimum": 0.1, "default": 4.5},
            "highlight_style": {
                "type": "string",
                "enum": ["none", "word_by_word", "karaoke"],
                "default": "none",
            },
            "corrections": {
                "type": "object",
                "description": (
                    "Dictionary of word corrections for common ASR misrecognitions. "
                    "Keys are the wrong word (case-insensitive), values are the "
                    "correct replacement. Applied before generating subtitles. "
                    "Example: {\"cloud\": \"Claude\", \"co-pilot\": \"Copilot\"}."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10)
    idempotency_key_fields = [
        "segments",
        "format",
        "max_words_per_cue",
        "grouping_mode",
        "semantic_break_after_word_indices",
        "protected_word_spans",
    ]
    side_effects = ["writes subtitle file to output_path"]
    user_visible_verification = [
        "Play video with generated subtitles and verify timing",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        segments = inputs["segments"]
        fmt = inputs.get("format", "srt")
        max_words = inputs.get("max_words_per_cue", 8)
        max_chars = inputs.get("max_chars_per_line", 42)
        highlight_style = inputs.get("highlight_style", "none")
        output_path = inputs.get("output_path")
        corrections = inputs.get("corrections")
        grouping_mode = inputs.get("grouping_mode", "fixed")

        start = time.time()

        # Apply word corrections if provided
        if corrections:
            segments = self._apply_corrections(segments, corrections)

        # Build cues from word-level timestamps
        if grouping_mode == "semantic":
            cues = self._build_semantic_cues(
                segments,
                max_words=max_words,
                max_chars_per_line=max_chars,
                max_lines=int(inputs.get("max_lines", 2)),
                min_duration=float(inputs.get("min_cue_duration_seconds", 1.5)),
                target_duration=float(inputs.get("target_cue_duration_seconds", 3.0)),
                max_duration=float(inputs.get("max_cue_duration_seconds", 4.5)),
                semantic_breaks=set(
                    inputs.get("semantic_break_after_word_indices", [])
                ),
                protected_spans=inputs.get("protected_word_spans", []),
            )
        else:
            cues = self._build_cues(segments, max_words, max_chars)

        if fmt == "srt":
            content = self._render_srt(cues, highlight_style)
            ext = ".srt"
        elif fmt == "vtt":
            content = self._render_vtt(cues, highlight_style)
            ext = ".vtt"
        elif fmt == "json":
            content = json.dumps({"cues": cues, "highlight_style": highlight_style}, indent=2)
            ext = ".caption.json"
        else:
            return ToolResult(success=False, error=f"Unknown format: {fmt}")

        if output_path is None:
            output_path = f"subtitles{ext}"
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")

        elapsed = time.time() - start

        return ToolResult(
            success=True,
            data={
                "format": fmt,
                "cue_count": len(cues),
                "output": str(out),
            },
            artifacts=[str(out)],
            duration_seconds=round(elapsed, 2),
        )

    @staticmethod
    def _semantic_words(segments: list[dict]) -> list[dict[str, Any]]:
        """Normalize transcript word shapes into one global timed word list."""

        words: list[dict[str, Any]] = []
        for segment in segments:
            raw_words = segment.get("words") or []
            if not raw_words and segment.get("text"):
                raw_words = [
                    {
                        "word": segment["text"],
                        "start": segment["start"],
                        "end": segment["end"],
                    }
                ]
            for raw in raw_words:
                text = str(raw.get("word", raw.get("text", ""))).strip()
                if not text:
                    continue
                if "start" in raw and "end" in raw:
                    start = float(raw["start"])
                    end = float(raw["end"])
                elif "start_ms" in raw and "end_ms" in raw:
                    start = float(raw["start_ms"]) / 1000
                    end = float(raw["end_ms"]) / 1000
                else:
                    raise ValueError(f"subtitle word {text!r} has no usable timing")
                if start < 0 or end <= start:
                    raise ValueError(f"subtitle word {text!r} has invalid timing")
                words.append({"word": text, "start": start, "end": end})

        previous_end = -1.0
        for position, word in enumerate(words):
            if word["start"] < previous_end:
                raise ValueError(
                    f"subtitle word {position} overlaps the previous word"
                )
            previous_end = word["end"]
        return words

    @staticmethod
    def _balanced_lines(
        words: list[dict[str, Any]],
        *,
        max_chars_per_line: int,
        max_lines: int,
    ) -> tuple[list[str], list[int]] | None:
        """Return balanced display lines and relative break indices."""

        texts = [str(word["word"]) for word in words]
        if not texts:
            return None

        candidates: list[tuple[tuple[int, int, int], list[str], list[int]]] = []

        def search(start: int, lines: list[str], breaks: list[int]) -> None:
            if start == len(texts):
                lengths = [len(line) for line in lines]
                key = (
                    len(lines),
                    max(lengths) - min(lengths),
                    max(lengths),
                )
                candidates.append((key, lines, breaks))
                return
            if len(lines) >= max_lines:
                return
            for end in range(start + 1, len(texts) + 1):
                line = " ".join(texts[start:end])
                if len(line) > max_chars_per_line:
                    break
                search(
                    end,
                    [*lines, line],
                    [*breaks, end - 1] if end < len(texts) else breaks,
                )

        search(0, [], [])
        if not candidates:
            return None
        _, lines, breaks = min(candidates, key=lambda candidate: candidate[0])
        return lines, breaks

    @staticmethod
    def _boundary_strength(word: str, index: int, semantic_breaks: set[int]) -> int:
        if index in semantic_breaks:
            return 0
        stripped = word.rstrip('"\'”’)]}')
        if stripped.endswith((".", "?", "!")):
            return 1
        if stripped.endswith((";", ":")):
            return 2
        if stripped.endswith(","):
            return 3
        return 8

    def _build_semantic_cues(
        self,
        segments: list[dict],
        *,
        max_words: int,
        max_chars_per_line: int,
        max_lines: int,
        min_duration: float,
        target_duration: float,
        max_duration: float,
        semantic_breaks: set[int],
        protected_spans: list[dict],
    ) -> list[dict]:
        """Plan pages globally using semantics, timing, and display capacity.

        Semantic hints are preferences rather than fixed buckets.  Dynamic
        programming can merge a short sentence with its neighbor or split a
        long sentence at the least costly safe point while preserving every
        canonical word exactly once.
        """

        words = self._semantic_words(segments)
        if not words:
            return []
        if min_duration > target_duration or target_duration > max_duration:
            raise ValueError(
                "caption durations must satisfy min <= target <= max"
            )

        protected_boundaries: set[int] = set()
        for span in protected_spans:
            start = int(span["startWordIndex"])
            end = int(span["endWordIndex"])
            if start < 0 or end <= start or end > len(words):
                raise ValueError("protected word span is out of range")
            protected_boundaries.update(range(start, end - 1))

        from functools import lru_cache

        @lru_cache(maxsize=None)
        def solve(start: int):
            if start == len(words):
                return (0.0, ())

            best = None
            upper = min(len(words), start + max_words)
            for end in range(start + 1, upper + 1):
                boundary_index = end - 1
                if end < len(words) and boundary_index in protected_boundaries:
                    continue

                duration = words[end - 1]["end"] - words[start]["start"]
                if duration > max_duration and end > start + 1:
                    break
                layout = self._balanced_lines(
                    words[start:end],
                    max_chars_per_line=max_chars_per_line,
                    max_lines=max_lines,
                )
                if layout is None:
                    continue
                lines, relative_breaks = layout
                tail = solve(end)
                if tail is None:
                    continue

                shortfall = max(0.0, min_duration - duration)
                duration_cost = (duration - target_duration) ** 2
                short_cost = shortfall * shortfall * 80
                boundary_cost = self._boundary_strength(
                    words[end - 1]["word"], boundary_index, semantic_breaks
                )
                # A hinted boundary should normally win over a visually valid
                # but syntactically arbitrary split.
                score = duration_cost + short_cost + boundary_cost + tail[0]
                candidate = (
                    score,
                    (
                        (
                            start,
                            end,
                            tuple(lines),
                            tuple(start + value for value in relative_breaks),
                        ),
                    )
                    + tail[1],
                )
                if best is None or candidate[0] < best[0]:
                    best = candidate
            return best

        solution = solve(0)
        if solution is None:
            raise ValueError(
                "caption text cannot fit the configured duration and layout limits"
            )

        cues = []
        for start, end, lines, break_indices in solution[1]:
            cue_words = words[start:end]
            cues.append(
                {
                    "index": len(cues) + 1,
                    "start": cue_words[0]["start"],
                    "end": cue_words[-1]["end"],
                    "text": " ".join(word["word"] for word in cue_words),
                    "lines": list(lines),
                    "startWordIndex": start,
                    "endWordIndex": end,
                    "lineBreakAfterWordIndices": list(break_indices),
                    "words": [dict(word) for word in cue_words],
                }
            )
        return cues

    @staticmethod
    def _apply_corrections(
        segments: list[dict], corrections: dict[str, str]
    ) -> list[dict]:
        """Apply word-level corrections to transcript segments.

        Handles case-insensitive matching and preserves punctuation.
        """
        import copy

        corr = {k.lower(): v for k, v in corrections.items()}
        result = copy.deepcopy(segments)

        for seg in result:
            words = seg.get("words", [])
            for w in words:
                raw = w.get("word", "").strip()
                # Strip punctuation for lookup, preserve it
                stripped = raw.lower().rstrip(".,!?;:'\"")
                if stripped in corr:
                    trailing = raw[len(stripped):]
                    w["word"] = corr[stripped] + trailing
            # Also fix segment-level text
            if "text" in seg and words:
                seg["text"] = " ".join(w["word"] for w in words)
            elif "text" in seg:
                for wrong, right in corr.items():
                    import re as _re
                    seg["text"] = _re.sub(
                        r"\b" + _re.escape(wrong) + r"\b",
                        right,
                        seg["text"],
                        flags=_re.IGNORECASE,
                    )

        return result

    def _build_cues(
        self, segments: list[dict], max_words: int, max_chars: int
    ) -> list[dict]:
        """Group words into display cues respecting max_words and max_chars."""
        # Collect all words with timestamps
        all_words = []
        for seg in segments:
            words = seg.get("words", [])
            if words:
                all_words.extend(words)
            elif "text" in seg:
                # Fallback: segment-level only (no word timestamps)
                all_words.append({
                    "word": seg["text"],
                    "start": seg["start"],
                    "end": seg["end"],
                })

        if not all_words:
            return []

        cues = []
        buf: list[dict] = []
        buf_text = ""

        for w in all_words:
            word_text = w["word"].strip()
            candidate = f"{buf_text} {word_text}".strip() if buf_text else word_text

            if buf and (len(buf) >= max_words or len(candidate) > max_chars):
                cues.append({
                    "index": len(cues) + 1,
                    "start": buf[0]["start"],
                    "end": buf[-1]["end"],
                    "text": buf_text,
                    "words": [
                        {"word": b["word"].strip(), "start": b["start"], "end": b["end"]}
                        for b in buf
                    ],
                })
                buf = []
                buf_text = ""

            buf.append(w)
            buf_text = f"{buf_text} {word_text}".strip() if buf_text else word_text

        # Flush remaining
        if buf:
            cues.append({
                "index": len(cues) + 1,
                "start": buf[0]["start"],
                "end": buf[-1]["end"],
                "text": buf_text,
                "words": [
                    {"word": b["word"].strip(), "start": b["start"], "end": b["end"]}
                    for b in buf
                ],
            })

        return cues

    def _render_srt(self, cues: list[dict], highlight_style: str = "none") -> str:
        lines = []
        if highlight_style == "word_by_word":
            # Emit one cue per word for word-by-word reveal
            idx = 1
            for cue in cues:
                for word_info in cue.get("words", []):
                    lines.append(str(idx))
                    lines.append(
                        f"{self._ts_srt(word_info['start'])} --> {self._ts_srt(word_info['end'])}"
                    )
                    lines.append(word_info["word"])
                    lines.append("")
                    idx += 1
        elif highlight_style == "karaoke":
            # Show full cue text but bold the active word using SRT HTML tags
            for cue in cues:
                words = cue.get("words", [])
                if not words:
                    lines.append(str(cue["index"]))
                    lines.append(f"{self._ts_srt(cue['start'])} --> {self._ts_srt(cue['end'])}")
                    lines.append(cue["text"])
                    lines.append("")
                    continue
                for wi, word_info in enumerate(words):
                    lines.append(str(cue["index"] * 100 + wi))
                    lines.append(
                        f"{self._ts_srt(word_info['start'])} --> {self._ts_srt(word_info['end'])}"
                    )
                    parts = []
                    for wj, w in enumerate(words):
                        if wj == wi:
                            parts.append(f"<b>{w['word']}</b>")
                        else:
                            parts.append(w["word"])
                    lines.append(" ".join(parts))
                    lines.append("")
        else:
            for cue in cues:
                lines.append(str(cue["index"]))
                lines.append(f"{self._ts_srt(cue['start'])} --> {self._ts_srt(cue['end'])}")
                lines.append(cue["text"])
                lines.append("")
        return "\n".join(lines)

    def _render_vtt(self, cues: list[dict], highlight_style: str = "none") -> str:
        lines = ["WEBVTT", ""]
        if highlight_style == "word_by_word":
            for cue in cues:
                for word_info in cue.get("words", []):
                    lines.append(
                        f"{self._ts_vtt(word_info['start'])} --> {self._ts_vtt(word_info['end'])}"
                    )
                    lines.append(word_info["word"])
                    lines.append("")
        elif highlight_style == "karaoke":
            for cue in cues:
                words = cue.get("words", [])
                if not words:
                    lines.append(f"{self._ts_vtt(cue['start'])} --> {self._ts_vtt(cue['end'])}")
                    lines.append(cue["text"])
                    lines.append("")
                    continue
                for wi, word_info in enumerate(words):
                    lines.append(
                        f"{self._ts_vtt(word_info['start'])} --> {self._ts_vtt(word_info['end'])}"
                    )
                    parts = []
                    for wj, w in enumerate(words):
                        if wj == wi:
                            parts.append(f"<b>{w['word']}</b>")
                        else:
                            parts.append(w["word"])
                    lines.append(" ".join(parts))
                    lines.append("")
        else:
            for cue in cues:
                lines.append(f"{self._ts_vtt(cue['start'])} --> {self._ts_vtt(cue['end'])}")
                lines.append(cue["text"])
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _hmsms(seconds: float) -> tuple[int, int, int, int]:
        """Decompose seconds into (h, m, s, ms), rounding to whole ms first.

        Rounding to total milliseconds before splitting the fields lets the
        carry propagate: 0.9995s+ must become the next second (…,000), not a
        malformed 4-digit …,1000 with the seconds field left unincremented.
        """
        total_ms = int(round(max(0.0, seconds) * 1000))
        h, rem = divmod(total_ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1_000)
        return h, m, s, ms

    @classmethod
    def _ts_srt(cls, seconds: float) -> str:
        """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
        h, m, s, ms = cls._hmsms(seconds)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @classmethod
    def _ts_vtt(cls, seconds: float) -> str:
        """Format seconds as VTT timestamp: HH:MM:SS.mmm"""
        h, m, s, ms = cls._hmsms(seconds)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
