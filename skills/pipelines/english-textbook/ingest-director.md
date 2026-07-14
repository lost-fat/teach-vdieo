# Ingest Director — English Textbook Pipeline

## When to Use

Use at the start of every run to turn non-empty plain English text into the immutable `lesson_source` contract.

## Process

1. Preserve the user input as `source_text`.
2. Apply only line-ending normalization, outer trim, repeated horizontal-whitespace collapse, or typographic-quote normalization, recording each operation.
3. Do not add, delete, reorder, or replace words; a suspicious spelling stays unchanged.
4. Compute SHA-256 over `normalized_text` as UTF-8 and store lowercase hexadecimal in `source_sha256`.
5. Validate `lesson_source.schema.json`, review the before/after text, and checkpoint it.

## Self-Evaluate

- Source and normalized text are non-empty.
- Every change appears in `normalizations_applied`.
- The hash is reproducible and `adaptation_mode` is `verbatim`.
- Word sequence is identical before and after normalization.

## Common Pitfalls

- Correcting textbook punctuation or spelling during ingest.
- Hashing a different representation than the persisted normalized text.
- Using an LLM for deterministic normalization.
