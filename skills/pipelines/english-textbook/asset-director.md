# Asset Director — English Textbook Pipeline

## When to Use

Use after scene approval to create the first-frame image and real ten-second motion clip under the locked DashScope quota contract.

## Process

1. Confirm the account-side free-tier stop setting and that the manifest still names the approved models.
2. Generate one image with `qwen-image-2.0-pro`, size `2688*1536`, `n=1`, `prompt_extend=false`, and `watermark=false`.
3. Inspect the downloaded image for 16:9 composition, no readable text, caption-safe framing, and playbook fidelity.
4. Submit that image to `wan2.6-i2v-flash` at `1080P`, duration `10`, `audio=false`, `prompt_extend=false`, and `watermark=false`.
5. Pass an explicit project-local `task_state_path`. Verify its `submitted` record contains the asynchronous task ID before polling so resume never creates a duplicate task.
6. Stop without retry or substitution on quota/billing/model errors.
7. Record prompt, model, provider, paths, duration, and zero charged spend in `asset_manifest`; checkpoint for asset review.

## Self-Evaluate

- Exactly the approved image and video models were used.
- Output files exist locally; temporary URLs are not the durable artifact.
- The task-state artifact is local, contains no signed URL, and can resume the original task ID.
- Video contains real motion and no provider-generated audio or captions.
- Asset manifest contains complete provenance with no secret.

## Common Pitfalls

- Retrying creation instead of polling the original task.
- Enabling Wan audio.
- Silently falling back to Ken Burns motion or another model.
