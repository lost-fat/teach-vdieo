# Edit Director — English Textbook Pipeline

## When to Use

Use to combine approved motion scenes, narration, translations, and canonical word timings into schema-valid `edit_decisions`.

## Process

1. Carry `lesson_plan.render.runtime` forward unchanged as `render_runtime`.
2. Create one primary cut per actual generated scene. Do not cut when only a subtitle page or narration unit changes. When one source clip spans several visual beats, express reframing through one continuous `transform.keyframes` curve inside that mounted source.
3. Put true scene changes at semantic pauses. Use a direct cut when motion and composition match; otherwise use a short dissolve or match edit. Never remount the same source solely to create wide/medium/close punch-ins.
4. Place narration at zero, retain approved pauses or end silence, and never trim spoken words.
5. Flatten canonical `narration_timeline.words` into the `captions` array. ASR supplies aligned time ranges only; canonical source spelling remains authoritative.
6. Run `subtitle_gen` with `grouping_mode: semantic`, grammar-safe boundary hints, and protected spans from the script. Plan each page from semantics, real word timing, and measured layout together—not a fixed word count. Use at most two centered English lines and preserve the returned `startWordIndex`, `endWordIndex`, and `lineBreakAfterWordIndices`.
7. Generate one natural Chinese teaching translation per complete English page, enforce the approved name glossary, and store it as that group's `translationText`. Translation may reorder clauses for natural Chinese but must not change facts or split a protected name/date phrase.
8. In article mode set composition duration from the measured narration timeline and scene edit. In fixed verification mode retain the strict ten-second metadata. Keep `expected_resolution: "1920x1080"`, `expected_video_codec: "h264"`, `strict_review: true`, the approved runtime, and `playbook: "esl-cinematic-editorial"`.
9. Validate complete one-time canonical word coverage and the `edit_decisions` schema before checkpointing.

## Self-Evaluate

- Runtime matches the approved lesson plan.
- Cut, audio, and caption ranges stay within the selected measured or fixed duration.
- Every canonical word appears in order and exactly once.
- Caption groups use planned word indices and line breaks; Chinese text is aligned page-by-page.
- Captions do not depend on an expiring external URL.

## Common Pitfalls

- Feeding raw ASR text to the renderer.
- Mutating the approved runtime in edit.
- Splitting one continuous source into adjacent hard cuts solely to create virtual-camera punch-ins; this remounts the decoder and can create visible stutters.
- Treating punctuation, narration chunks, or caption pages as automatic video cuts.
- Hiding an overlong narration by clipping its ending.
