# Compose Director — English Textbook Pipeline

## When to Use

Use to render and technically verify the approved English textbook composition in fixed verification or article mode.

## Process

1. Read `edit_decisions.render_runtime`; never switch runtimes silently. Remotion is recommended for the existing deterministic word-caption layer. If the locked value is HyperFrames, verify equivalent caption support first; if unavailable, return to the idea decision rather than substituting.
2. Stage local video and narration assets and construct word-caption props from canonical aligned words.
3. Require locked strict metadata (`target_duration_seconds`, `duration_tolerance_seconds`, `expected_resolution: "1920x1080"`, `expected_video_codec: "h264"`, and `strict_review: true`) and render at 30 fps with narration audio and the approved music decision.
4. Probe the actual MP4: require H.264 video, an audio stream, and duration within the declared tolerance. The 9.90–10.10 second window applies only to `phase1_verification`; article mode uses the measured edit duration.
5. Sample opening, middle, and ending frames; inspect motion, title-safe captions, active-word highlighting, black frames, and visual continuity.
6. Transcribe or audit rendered audio against the source and complete `final_review`; do not present output unless status is `pass`.
7. Validate `render_report` and checkpoint both artifacts.

## Self-Evaluate

- The runtime used equals the approved `render_runtime`.
- Technical probe, visual review, audio review, and subtitle integrity review all pass.
- Captions show canonical words with deterministic active timing.
- Output is self-contained and contains no credentials.

## Common Pitfalls

- Treating successful encoding as sufficient review.
- Omitting the audio-stream check.
- Switching from Remotion to HyperFrames or FFmpeg without an approved decision.
