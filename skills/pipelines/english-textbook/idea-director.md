# Idea Director — English Textbook Pipeline

## When to Use

Use after ingest to lock the audience, delivery mode, learning presentation, API models, quota policy, music choice, and composition approach in `brief`, `lesson_plan`, and `decision_log`.

## Process

1. Read `lesson_source`; never rewrite its canonical passage.
2. Lock the manifest API contract exactly: DashScope Beijing, `qwen3.7-plus`, `qwen-image-2.0-pro`, `wan2.6-i2v-flash`, `qwen3-tts-vd-2026-01-26`, and free-tier-only spend.
3. **Present both** Remotion and hyperframes when available before setting `render_runtime`. Recommend Remotion for deterministic word highlighting, but do not silently pick it. Record a `render_runtime_selection` decision, including why HyperFrames was accepted or rejected.
4. Present templated versus atelier authoring; recommend templated for the repeatable textbook workflow.
5. Set `delivery_mode` from the requested output: `phase1_verification` only for an explicit fixed validation render, otherwise `article`. For article mode use `duration_policy.mode: narration_measured`; `target_duration_seconds` is an estimate until narration is measured, not a reason to speed up or truncate the source.
6. Record provider-preflight clip limits in `duration_policy`. Prefer a ten-second clip target to reduce needless cuts, while treating the current provider limit as the hard maximum for any single generated clip.
7. Keep music at `none`, captions at `word_highlight`, and the playbook at `esl-cinematic-editorial` unless the user changes those decisions.
8. Validate `brief` and `lesson_plan`, then checkpoint as `awaiting_human` unless recorded full-run approval applies.

## Self-Evaluate

- Models, region, delivery mode, duration policy, and zero-paid-spend policy match the manifest.
- Runtime and authoring alternatives were surfaced and logged.
- The lesson plan references the exact source hash.
- No generation call has happened yet.

## Common Pitfalls

- Confusing a zero paid cap with permission to switch to another paid model.
- Silently defaulting to a runtime.
- Treating source correction as part of creative planning.
