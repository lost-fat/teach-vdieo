# Compose Director — English Textbook Pipeline

## When to Use

Use to render and verify the approved ten-second English textbook composition.

## Process

1. Read `edit_decisions.render_runtime`; never switch runtimes silently. Remotion is recommended for the existing deterministic word-caption layer. If the locked value is HyperFrames, verify equivalent caption support first; if unavailable, return to the idea decision rather than substituting.
2. Stage local video and narration assets and construct word-caption props from canonical aligned words.
3. Require the locked strict metadata (`target_duration_seconds: 10`, `duration_tolerance_seconds: 0.1`, `expected_resolution: "1920x1080"`, `expected_video_codec: "h264"`, and `strict_review: true`) and render at 30 fps with narration audio and no music for Phase 1.
4. Probe the actual MP4: require H.264 video, an audio stream, and duration from 9.90 through 10.10 seconds.
5. Sample opening, middle, and ending frames; inspect motion, title-safe captions, active-word highlighting, black frames, and visual continuity.
6. Transcribe or audit rendered audio against the source and complete `final_review`; do not present output unless status is `pass`.
7. Validate `render_report` and checkpoint both artifacts.

## Self-Evaluate

- The runtime used equals the approved `render_runtime`.
- Technical probe, visual review, audio review, and subtitle review all pass.
- Captions show canonical words with deterministic active timing.
- Output is self-contained and contains no credentials.

## Common Pitfalls

- Treating successful encoding as sufficient review.
- Omitting the audio-stream check.
- Switching from Remotion to HyperFrames or FFmpeg without an approved decision.
