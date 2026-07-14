# Executive Producer — English Textbook Pipeline

## When to Use

Use this pipeline for a plain English textbook passage that must remain verbatim while becoming a narrated, contextual video with word-highlight captions.

## Prerequisites

Read `pipeline_defs/english-textbook.yaml`, the Phase 1 spec, every current stage director, `meta/reviewer`, and `meta/checkpoint-protocol`. Preflight the locked DashScope models, free-tier-only policy, FFmpeg, and the selected composition runtime before any live generation.

## Process

1. Initialize the project and always pass `pipeline_type="english-textbook"` to checkpoints.
2. Execute `ingest → idea → script → narration → scene_plan → assets → edit → compose → publish` in order.
3. Treat `lesson_source.source_sha256` as the cross-stage identity; send back any artifact that references a different hash.
4. Keep narration audio-first: no scene timing is final before the measured `narration_timeline` exists.
5. Enforce the zero-paid-spend contract. A quota or model availability error stops the run and asks the user for a model change; never substitute automatically.
6. At every gate, write `awaiting_human`, present the artifact and evidence, and end the turn unless a recorded full-run approval covers that exact model path.
7. Before delivery, require `final_review.status == "pass"` and a measured 9.90–10.10 second verification MP4.

## Self-Evaluate

- Every completed stage has its schema-valid canonical artifact.
- The source hash, model IDs, runtime, and quota policy remain unchanged.
- No paid fallback or provider substitution occurred.
- Final evidence covers video, audio, captions, duration, and secret redaction.

## Common Pitfalls

- Planning visuals before measuring narration.
- Treating ASR words as canonical text.
- Continuing after a free-tier exhaustion response.
- Skipping a manifest-defined approval gate.
