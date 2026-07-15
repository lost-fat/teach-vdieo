# English Textbook Video — Article Mode Spec

**Status:** implemented planning/runtime contract  
**Pipeline:** `english-textbook`  
**Delivery mode:** `article`  
**Composition:** Remotion, templated, bilingual captions, no music by default

## Goal

Given one complete English textbook article, preserve every canonical source word and produce a natural narrated video whose duration follows the measured narration. The pipeline must not assume one sentence, one ten-second output, one caption size, or one generated clip.

The existing `phase1_verification` mode remains available for an explicitly fixed ten-second provider check. Its duration rules must not leak into article mode.

## Source and narration contract

1. Ingest locks the normalized source and SHA-256 before creative planning.
2. Qwen may annotate discourse roles, grammar-safe boundaries, protected spans, pronunciation, and named entities. It may not rewrite canonical narration.
3. Long articles may be synthesized in provider-safe chunks, but every chunk uses the same approved voice and delivery profile.
4. Canonical words are aligned to measured TTS/ASR times and flattened into one monotonic article timeline.
5. Article duration is the measured narration/edit duration. There is no global ten-second compression target.

## Semantic bilingual caption planning

Caption pages are selected from three inputs together:

- language structure: clauses, punctuation, model/parser boundary hints, and protected phrases;
- real audio timing: minimum, target, and maximum readable page duration;
- rendered layout: at most two lines and a configured character width per line.

The deterministic planner uses dynamic programming to choose the lowest-cost valid page sequence. It preserves every canonical word exactly once and emits absolute `startWordIndex`, `endWordIndex`, and `lineBreakAfterWordIndices` values.

Very short sentences are merged with adjacent context when showing them alone would be too brief. Very long sentences are split at the best grammar-safe boundary that fits timing and layout. Proper names, dates, appositives, and other protected spans cannot be split internally. A fixed “six words per page” rule is not used.

Chinese is translated once per complete English meaning page. Approved named-entity translations are supplied as a glossary, and the Chinese may use natural word order instead of mirroring English syntax. `translationText` travels with the caption group so page timing, English segmentation, and Chinese meaning cannot drift apart.

## Scene and clip planning

`narrative_units` describe what the article means over time. They are not clip requests. The scene planner groups adjacent units while subject, location, time period, and visual logic remain continuous.

A new generated clip is created only when:

- the subject, place, time, or visual treatment genuinely changes; or
- the current provider's verified maximum clip duration would be exceeded.

Subtitle pages and narration chunk boundaries never create cuts. Within one clip, wide-to-medium-to-close progression is expressed as timed action/camera beats or a single Remotion keyframe curve. A true scene change is placed near a semantic pause and uses a direct match cut or short dissolve as appropriate.

## Continuity contract

Each article owns one `continuity_bible` containing:

- canonical entities and locations with stable IDs and immutable traits;
- approved translations;
- period facts;
- palette, lighting, and texture;
- camera rules and prohibited elements.

Every scene references these IDs. First-frame generation reuses approved reference assets where possible. A continuing action may use the previous approved end frame as its next first-frame reference; a deliberate angle or context change keeps the shared identity anchors without forcing the same composition.

## Structured motion prompt contract

Creative planning writes a provider-neutral `video_prompt_spec` rather than a single prose blob:

- `single_shot`
- `subject_motion`
- `camera_motion`
- timed `temporal_beats`
- optional foreground/parallax event
- optional visual payoff
- `continuity_refs`
- caption-safe area
- negative constraints

`build_video_prompt(..., provider="wan-i2v")` resolves continuity IDs, prioritizes temporal motion, and compiles separate positive and negative prompt fields within provider length limits. This keeps the creative plan portable and prevents negative instructions from weakening the desired action.

## Compatibility

All new schema fields are optional for existing Phase 1 artifacts. Legacy time-only caption groups still render; word-indexed groups take precedence when present. Existing ten-second tests and renders remain valid.

## Current scope boundary

This change does not add the proposed chapter-level “interesting and natural” scoring rubric or final QA metrics. It improves the planning and rendering path itself. It also performs no live image, video, TTS, ASR, or text-generation API call.

