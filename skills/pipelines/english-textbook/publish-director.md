# Publish Director — English Textbook Pipeline

## When to Use

Use after a passing final review to package the fixed verification or full article MP4 and its audit trail without publishing externally by default.

## Process

1. Confirm `final_review.status` is `pass` and references the same output as `render_report`.
2. Package the MP4, source hash, model IDs, runtime, measured duration, and verification notes.
3. Remove temporary provider URLs, request headers, credentials, and local secret-file references.
4. State the artifact's `delivery_mode`, measured duration, and any known limitations. Do not label article output as a ten-second Phase 1 verification.
5. Validate `publish_log`, checkpoint as `awaiting_human`, and wait for delivery approval.

## Self-Evaluate

- Package contains the verified output and enough provenance to reproduce it.
- No API key or temporary download URL is exposed.
- Duration, resolution, codec, and model labels match evidence.
- No external upload occurred without explicit authorization.

## Common Pitfalls

- Treating package preparation as permission to upload.
- Omitting the source hash or verification warnings.
- Shipping temporary URLs instead of local assets.
