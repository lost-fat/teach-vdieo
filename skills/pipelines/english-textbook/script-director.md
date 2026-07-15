# Script Director — English Textbook Pipeline

## When to Use

Use to express a short passage or full article as ordered, grammar-safe narration units without changing its wording.

## Process

1. Verify `lesson_source` and `lesson_plan` share the same hash.
2. Use `qwen3.7-plus` only for structured boundary planning; canonical narration text comes from `normalized_text`.
3. Ask the model for structured boundary hints, protected spans, discourse roles, pronunciation guidance, and named-entity translation candidates. Treat these as annotations over canonical source offsets, never replacement text.
4. Segment by meaning and provider-safe narration size. Merge extremely short sentences with adjacent context when the discourse remains continuous; split long sentences only at clauses or other grammar-safe boundaries. Never orphan an article, preposition, conjunction, subordinate marker, proper name, date, or appositive.
5. Narration units are audio and teaching units, not generated-video boundaries. Keep adjacent units visually mergeable and defer final shot grouping to the scene stage after audio timing exists.
6. Store source character ranges and pronunciation guidance in the canonical `script` structure.
7. Verify ordered ranges cover every non-interstitial source character once, validate, and checkpoint.

## Self-Evaluate

- Concatenated narration preserves canonical word order.
- Ranges contain no overlap, gap, or out-of-bounds index.
- Boundaries are grammatical rather than chosen for visual convenience.
- Script duration is plausible for the selected delivery mode and remains secondary to source fidelity.

## Common Pitfalls

- Paraphrasing to make TTS shorter.
- Splitting at arbitrary duration buckets.
- Locking final timings before synthesis.
