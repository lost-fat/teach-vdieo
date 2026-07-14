# Narration Director — English Textbook Pipeline

## When to Use

Use after script approval to synthesize the canonical passage, measure real audio, and build `narration_timeline` with source-aligned word timestamps.

## Process

1. Confirm source hashes match and the approved TTS target is `qwen3-tts-vd-2026-01-26`.
2. Reuse a cached compatible `english_teacher_female` voice before creating one. Never reuse a voice made for an incompatible target model.
3. Synthesize canonical section text and persist both the Voice Design preview (when a voice is first created) and narration WAV immediately. Stop on model-unavailable, billing, or free-tier exhaustion errors.
4. Measure actual duration. Do not infer it from character count.
5. Run `qwen3-asr-flash-filetrans` with `language: en`, word timing enabled, and a distinct project-local `task_state_path`. Preserve a URL-free raw-ASR QA transcript, then use `lib.lesson_alignment` to align normalized tokens back to canonical source tokens. Canonical spelling controls captions; raw ASR remains the independent content check.
6. Add silence padding when under ten seconds. A tempo adjustment up to 1.15x requires intelligibility review; never trim words.
7. Validate `narration_timeline`. Its checkpoint must carry both the unchanged locked `lesson_source` and the timeline so cross-stage source fidelity is verified.

## Self-Evaluate

- Audio exists and uses the approved voice/model combination.
- A newly created voice has a locally persisted preview artifact.
- Canonical words appear once, in order, with monotonic times.
- Actual duration and padding/tempo decisions are recorded.
- No retry can create a duplicate billable voice, narration, or ASR task.

## Common Pitfalls

- Replacing source words with ASR guesses.
- Creating a new custom voice on every run.
- Estimating rather than probing audio duration.
