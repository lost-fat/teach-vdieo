# Publish Director — English Textbook Pipeline

## When to Use

Use after a passing final review to package the verification MP4 and its audit trail without publishing externally by default.

## Process

1. Confirm `final_review.status` is `pass` and references the same output as `render_report`.
2. Package the MP4, source hash, model IDs, runtime, measured duration, and verification notes.
3. Remove temporary provider URLs, request headers, credentials, and local secret-file references.
4. State that the artifact is a Phase 1 ten-second verification and preserve any known limitations.
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
