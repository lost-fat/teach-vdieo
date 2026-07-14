# Script Director — English Textbook Pipeline

## When to Use

Use to express the locked source as timed, grammar-safe narration sections without changing its wording.

## Process

1. Verify `lesson_source` and `lesson_plan` share the same hash.
2. Use `qwen3.7-plus` only for structured boundary planning; canonical narration text comes from `normalized_text`.
3. Prefer 2.5–7.0 second units. Split only at grammar-safe boundaries and never orphan an article, preposition, coordinating conjunction, or subordinate marker.
4. When a single sentence should remain continuous, keep one narration unit and defer multiple visuals to `visual_beats`.
5. Store source character ranges and pronunciation guidance in the canonical `script` structure.
6. Verify ordered ranges cover every non-interstitial source character once, validate, and checkpoint.

## Self-Evaluate

- Concatenated narration preserves canonical word order.
- Ranges contain no overlap, gap, or out-of-bounds index.
- Boundaries are grammatical rather than chosen for visual convenience.
- Script duration is plausible for the ten-second target.

## Common Pitfalls

- Paraphrasing to make TTS shorter.
- Splitting at arbitrary duration buckets.
- Locking final timings before synthesis.
