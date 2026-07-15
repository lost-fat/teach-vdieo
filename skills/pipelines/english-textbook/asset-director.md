# Asset Director — English Textbook Pipeline

## When to Use

Use after scene approval to create continuity-controlled first frames and real motion clips under the locked DashScope quota contract.

## Process

1. Confirm the account-side free-tier stop setting and that the manifest still names the approved models.
2. Resolve each scene's continuity IDs against `continuity_bible` and generate its 16:9 first frame with `qwen-image-2.0-pro`, size `2688*1536`, `n=1`, `prompt_extend=false`, and `watermark=false`. Reuse approved character/location reference assets where available; do not rely on free-form prompt repetition alone.
3. Inspect the downloaded image for composition, canonical entity/location traits, no readable text, caption-safe framing, and playbook fidelity. When an adjacent scene intentionally continues the same action, prefer an approved previous end frame as the next first-frame reference; otherwise keep the shared bible but author the new composition deliberately.
4. Compile `scene.video_prompt_spec` with `build_video_prompt(scene, continuity_bible, provider="wan-i2v")`. Send the resulting `prompt` and `negative_prompt` separately so exclusions do not dilute the motion instructions.
5. Submit the frame to `wan2.6-i2v-flash` at `1080P`, using the scene's narration-aligned duration within `duration_policy.clip_max_seconds`, with `audio=false`, `prompt_extend=false`, and `watermark=false`. The fixed verification mode remains exactly ten seconds.
6. Pass a distinct project-local `task_state_path` per scene. Verify its `submitted` record contains the asynchronous task ID before polling so resume never creates a duplicate task.
7. Stop without retry or substitution on quota/billing/model errors.
8. Record structured prompt input, compiled positive and negative prompts, continuity refs, model, provider, paths, duration, and zero charged spend in `asset_manifest`; checkpoint for asset review.

## Self-Evaluate

- Exactly the approved image and video models were used.
- Output files exist locally; temporary URLs are not the durable artifact.
- The task-state artifact is local, contains no signed URL, and can resume the original task ID.
- Every video contains real motion and no provider-generated audio or captions.
- Asset manifest contains complete provenance with no secret.

## Common Pitfalls

- Retrying creation instead of polling the original task.
- Enabling Wan audio.
- Silently falling back to Ken Burns motion or another model.
