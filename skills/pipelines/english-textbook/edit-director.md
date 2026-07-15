# Edit Director — English Textbook Pipeline

## When to Use

Use to combine the approved motion asset, narration, and canonical word timings into schema-valid `edit_decisions`.

## Process

1. Carry `lesson_plan.render.runtime` forward unchanged as `render_runtime`.
2. Set the motion clip as one primary cut from 0 to 10 seconds. When one source spans multiple visual beats, express wide-to-medium-to-close reframing through `transform.keyframes` inside that single cut; do not remount the same source merely to change framing.
3. Place narration at zero; retain any approved end silence and do not trim spoken words.
4. Generate caption data from canonical `narration_timeline.words`, using ASR only for their aligned time ranges.
5. Configure subtitles as enabled, `word-by-word`, English, title-safe, high contrast, and no more than six words per page.
6. Set composition metadata explicitly: `target_duration_seconds: 10`, `duration_tolerance_seconds: 0.1`, `expected_resolution: "1920x1080"`, `expected_video_codec: "h264"`, `strict_review: true`, `proposal_render_runtime: "remotion"`, and `playbook: "esl-cinematic-editorial"`.
7. Validate the complete caption coverage and `edit_decisions` schema before checkpointing.

## Self-Evaluate

- Runtime matches the approved lesson plan.
- Cut, audio, and caption time ranges stay within ten seconds.
- Every canonical word appears in order and exactly once.
- Captions do not depend on an expiring external URL.

## Common Pitfalls

- Feeding raw ASR text to the renderer.
- Mutating the approved runtime in edit.
- Splitting one continuous source into adjacent hard cuts solely to create virtual-camera punch-ins; this remounts the decoder and can create visible stutters.
- Hiding an overlong narration by clipping its ending.
