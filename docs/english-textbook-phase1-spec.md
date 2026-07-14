# English Textbook Video — Phase 1 Acceptance Brief

**Status:** Approved for implementation; live render pending runtime preflight  
**Revision:** 1  
**Date:** 2026-07-14  
**Pipeline ID:** `english-textbook`  
**Verification deliverable:** one 10-second, 16:9, 1080p MP4

## Revision Log

| Revision | Date | Changed criteria | Reason |
| --- | --- | --- | --- |
| 1 | 2026-07-14 | Initial acceptance contract | Provider, region, model, and quota constraints confirmed by the user |

## Goal

Given a short English textbook passage, produce a 10-second narrated video that preserves the source text, synchronizes visual motion and word-level English captions to the narration, and can be resumed from OpenMontage checkpoints.

## User Journey

As an English learner or teacher, I want to turn a passage into a narrated visual scene so that the language can be heard, read, and understood in context without changing the textbook wording.

## Scope

### In scope

- Plain English text input.
- Source normalization and SHA-256 source locking.
- Grammar-safe narration units with source character ranges.
- Audio-first planning: synthesize narration and measure its real duration before locking scenes.
- Word-level English caption timing.
- One or more visual beats mapped to a continuous narration unit.
- DashScope text, voice design/TTS, image, ASR, and image-to-video providers.
- One 10-second 1080p validation render.
- Checkpoint-resumable OpenMontage artifacts and generated assets.
- Mocked provider tests that do not consume quota.

### Out of scope

- PDF/OCR ingestion.
- Chinese translation subtitles.
- Vocabulary cards, IPA, grammar instruction, quizzes, or repeat-after-me pauses.
- Multiple speakers or character dialogue.
- Batch generation across a textbook.
- User-facing web forms.
- Automatic provider or model substitution.
- Background music for the Phase 1 verification render; the narration and caption timing are the audio acceptance target.

## Confirmed Product Constraints

The following constraints were supplied by the user and are binding for Phase 1:

- Provider: Alibaba Cloud Model Studio / DashScope.
- Deployment region: China (Beijing / `cn-beijing`).
- Text model: `qwen3.7-plus`.
- Image model: `qwen-image-2.0-pro`.
- Video model: `wan2.6-i2v-flash`.
- Voice design and synthesis target model: `qwen3-tts-vd-2026-01-26`.
- Existing `DASHSCOPE_API_KEY` from ContentMachine may be reused locally.
- Paid spend cap: zero. Free quota may be consumed; no paid fallback is allowed.
- If a selected model is unavailable or its free quota is exhausted, stop and request a model change.
- Verification output duration: 10 seconds.

## Discovered Technical Facts

- OpenMontage is instruction-driven: a manifest and stage director skills orchestrate BaseTool provider implementations.
- `qwen-image-2.0-pro` supports a 16:9 `2688*1536` output and synchronous image generation.
- `wan2.6-i2v-flash` supports integer durations from 2 through 15 seconds at 720P or 1080P.
- Wan 2.6 can generate audio, but Phase 1 must set `audio=false`; the verbatim TTS track remains the canonical narration.
- `qwen3-tts-vd-2026-01-26` requires a custom voice created for the same target model. A voice created for a realtime target model cannot be reused.
- DashScope voice creation returns a voice ID that must be cached and reused.
- OpenMontage already has DashScope image, TTS, and ASR foundations, but lacks DashScope text and Wan cloud video provider tools and does not yet support the selected Voice Design target.
- The current machine has Python and Node.js, but a complete FFmpeg/ffprobe pair and composer dependencies must pass preflight before live rendering.

## External API Contract

```yaml
provider: dashscope
region: cn-beijing
text:
  model: qwen3.7-plus
  response_format: json_object
image:
  model: qwen-image-2.0-pro
  size: 2688*1536
  n: 1
  prompt_extend: false
  watermark: false
voice:
  design_model: qwen-voice-design
  target_model: qwen3-tts-vd-2026-01-26
  profile: english_teacher_female
  language: en
  sample_rate: 24000
tts:
  model: qwen3-tts-vd-2026-01-26
  language_type: English
asr:
  model: qwen3-asr-flash-filetrans
  enable_words: true
video:
  model: wan2.6-i2v-flash
  resolution: 1080P
  duration: 10
  audio: false
  prompt_extend: false
  watermark: false
```

### Quota safety

- Unit and contract tests must mock every network request.
- The live verification may create at most one new custom voice, one narration, one image, one ASR job, and one 10-second video task.
- Voice creation must first check the local cache and optionally list compatible voices before creating a new one.
- `AllocationQuota.FreeTierOnly`, free-tier exhaustion, billing authorization, or equivalent quota errors are terminal and must not be retried.
- The implementation must never silently switch to a paid model, a different provider, or a different model family.
- Because a normal inference response does not expose the account's remaining free quota, a strict zero-spend guarantee also requires the account-side "stop when free quota is exhausted" setting before the live run.

## Pipeline

```text
ingest
  -> idea
  -> script
  -> narration
  -> scene_plan
  -> assets
  -> edit
  -> compose
  -> publish
```

### Stage contracts

| Stage | Required input | Canonical output | Gate |
| --- | --- | --- | --- |
| `ingest` | Plain English text | `lesson_source` | Automatic |
| `idea` | `lesson_source` | `brief`, `lesson_plan`, `decision_log` | Human by default |
| `script` | `lesson_source`, `lesson_plan` | `script` | Human by default |
| `narration` | `lesson_source`, `script` | `narration_timeline` and narration audio | Human by default |
| `scene_plan` | `narration_timeline`, `lesson_plan` | `scene_plan` | Human by default |
| `assets` | `scene_plan`, `narration_timeline` | `asset_manifest` | Human by default |
| `edit` | `asset_manifest`, `scene_plan`, `narration_timeline` | `edit_decisions` | Automatic |
| `compose` | `edit_decisions`, `asset_manifest` | `render_report`, `final_review` | Automatic |
| `publish` | `render_report`, `final_review` | `publish_log` | Human by default |

The one-off 10-second verification may use a recorded full-run approval decision for the exact provider/model path above. That approval does not authorize model substitutions or later batch runs.

## Artifact Contracts

### `lesson_source`

```json
{
  "version": "1.0",
  "language": "en",
  "source_text": "Exact user-provided passage.",
  "normalized_text": "Exact normalized passage.",
  "source_sha256": "64 lowercase hex characters",
  "adaptation_mode": "verbatim",
  "normalizations_applied": ["trim_outer_whitespace"]
}
```

Allowed normalization is limited to line-ending normalization, removal of outer whitespace, collapse of repeated horizontal whitespace, and typographic quote normalization. Words may not be added, removed, reordered, or replaced.

### `lesson_plan`

Required fields:

- Target audience and English level.
- Exact duration target.
- Caption mode.
- Voice profile.
- Visual style playbook.
- Provider/model selections.
- Render runtime and authoring mode decision.
- Music decision.
- Free-quota-only policy.

### `narration_timeline`

```json
{
  "version": "1.0",
  "source_sha256": "...",
  "total_duration_ms": 8420,
  "units": [
    {
      "id": "nu-001",
      "source_text": "...",
      "source_start_char": 0,
      "source_end_char": 98,
      "audio_asset_id": "narration-nu-001",
      "audio_path": "projects/.../assets/audio/narration-nu-001.wav",
      "actual_duration_ms": 8420,
      "words": [
        {"text": "Every", "start_ms": 0, "end_ms": 330}
      ],
      "visual_beats": [
        {"id": "vb-001", "start_ms": 0, "end_ms": 4200, "visual_intent": "..."},
        {"id": "vb-002", "start_ms": 4200, "end_ms": 8420, "visual_intent": "..."}
      ]
    }
  ]
}
```

Narration unit rules:

- Prefer 2.5–7.0 seconds per unit.
- Merge units shorter than 2.5 seconds when grammar permits.
- Split units longer than 7.0 seconds only at grammar-safe boundaries.
- If splitting would create a fragment, retain continuous narration and map it to multiple visual beats.
- A coordinating conjunction, preposition, article, or subordinate marker must not be orphaned at a unit boundary.
- Every normalized source character must be covered once and only once, excluding normalized whitespace between ranges.

## Visual Contract

Playbook ID: `esl-cinematic-editorial`.

- Mature cinematic editorial documentary illustration.
- Semi-realistic 2D treatment; not a juvenile picture-book style.
- Full-bleed 16:9 composition with natural light and restrained cinematic grading.
- Stable character identity, clothing, palette, geography, and period details.
- No readable text generated inside image/video assets.
- Text and captions are rendered only by the composition runtime.
- Image prompt rewriting is disabled for source and style fidelity.
- Wan output is a real 10-second motion clip; a still-image/Ken Burns substitute is not an automatic fallback.

## Caption Contract

- The caption text is derived from `lesson_source.normalized_text`, not from ASR transcription.
- ASR supplies timestamps only.
- Normalized ASR tokens are aligned back to canonical source tokens.
- Each displayed word has `start_ms` and `end_ms`.
- Active-word highlighting uses a deterministic frame calculation.
- Captions remain within the 16:9 title-safe region and do not cover the primary subject when avoidable.
- Phase 1 contains one English caption track only.

## Composition Decision

The live run must perform the mandatory OpenMontage runtime preflight.

- Recommended runtime: Remotion, because the existing React caption layer already supports deterministic word-level highlighting.
- Recommended authoring mode: templated, because this is a repeatable textbook workflow rather than a one-off hero film.
- HyperFrames must still be presented if installed; it is strongest for custom kinetic typography but would add unnecessary authoring work for this verification.
- FFmpeg remains the encoding, muxing, and verification floor, not the approved visual authoring runtime.
- Recommended music decision for Phase 1: no music, so narration intelligibility and word timing can be judged without masking.

The runtime and music recommendation must be explicitly approved before the first live asset generation call.

## Acceptance Criteria

### AC-001: Source text is locked before planning

- **Scenario:** A non-empty English passage is provided.
- **Action:** Run the `ingest` stage.
- **Expected:** `lesson_source` contains source text, normalized text, normalization log, `verbatim` mode, and a reproducible SHA-256 hash.
- **Must not:** Add, delete, reorder, or replace words.
- **Verification:** JSON Schema validation plus source normalization unit tests.
- **Priority:** Required.

### AC-002: Narration preserves grammar and source coverage

- **Scenario:** A schema-valid `lesson_source` exists.
- **Action:** Produce `script` and `narration_timeline`.
- **Expected:** Units cover the complete normalized source in order, contain valid character ranges, and retain grammar-safe boundaries.
- **Must not:** Split solely to match a visual cut or model duration bucket.
- **Verification:** Contract tests for exact source coverage, overlaps, gaps, and invalid fragments; manual review of the verification fixture.
- **Priority:** Required.

### AC-003: Voice Design is created once and reused

- **Scenario:** The selected profile has no cached voice for `qwen3-tts-vd-2026-01-26`.
- **Action:** Execute DashScope TTS.
- **Expected:** One compatible voice is created, its preview is persisted, and its voice ID is cached by target model and profile; later runs reuse it.
- **Must not:** Reuse the realtime-model voice ID or create a new voice on every narration call.
- **Verification:** Mocked HTTP tests for cache miss, cache hit, target mismatch, malformed response, and quota error.
- **Priority:** Required.

### AC-004: DashScope image request follows the locked visual contract

- **Scenario:** An approved visual prompt exists.
- **Action:** Generate the first-frame image.
- **Expected:** Request uses `qwen-image-2.0-pro`, `2688*1536`, `n=1`, `prompt_extend=false`, and `watermark=false`; the downloaded artifact is a valid 16:9 image.
- **Must not:** Generate provider-rendered captions or readable text.
- **Verification:** Payload unit test, mocked download integration test, and visual review of the live fixture.
- **Priority:** Required.

### AC-005: Wan provider produces the approved motion contract

- **Scenario:** An approved first-frame image is available as an accepted URL or data URI.
- **Action:** Execute DashScope video generation.
- **Expected:** One asynchronous task uses `wan2.6-i2v-flash`, 1080P, duration 10, `audio=false`, `prompt_extend=false`, and `watermark=false`; a project-local task-state file durably records the task ID without its signed output URL, polling resumes from that ID, and one MP4 is downloaded.
- **Must not:** Create duplicate tasks during polling, enable generated audio, retry free-tier exhaustion, or switch models.
- **Verification:** Mocked create/poll/download tests and live ffprobe validation.
- **Priority:** Required.

### AC-006: Captions use canonical words and aligned timestamps

- **Scenario:** Narration audio and ASR word timestamps exist.
- **Action:** Build caption data and compose the video.
- **Expected:** Every canonical word appears in order and active-word timing maps to video frames; contractions split by ASR and spoken-year variants such as `1901` are positionally aligned, while a separate URL-free raw-ASR transcript remains available for independent narration QA.
- **Must not:** Replace source words with ASR guesses.
- **Verification:** Token-alignment unit tests and frame-level caption fixture tests.
- **Priority:** Required.

### AC-007: The verification deliverable is a valid 10-second MP4

- **Scenario:** Approved assets, edit decisions, and composition runtime are available.
- **Action:** Run `compose` for the verification fixture.
- **Expected:** Strict review metadata locks the render to 1920×1080, H.264 MP4, 30 fps, an audio stream, complete canonical caption coverage, and a measured duration between 9.90 and 10.10 seconds; any issue returns `revise` rather than `pass`.
- **Must not:** Depend on files outside the project workspace or expose credentials in metadata/logs.
- **Verification:** ffprobe JSON assertions, visual frame sampling, and an audible playback review.
- **Priority:** Required.

### AC-008: Existing OpenMontage behavior remains compatible

- **Scenario:** Phase 1 changes are installed.
- **Action:** Run the existing contract and unit suites.
- **Expected:** Existing pipelines and provider tools continue to load and their tests remain green.
- **Must not:** Change defaults for unrelated pipelines.
- **Verification:** Relevant full pytest suite plus targeted coverage report of new modules at 80% or higher.
- **Priority:** Required.

### AC-009: Quota exhaustion stops the run

- **Scenario:** DashScope returns a free-tier-only, allocation quota, billing authorization, or model-unavailable response.
- **Action:** A provider tool handles the response.
- **Expected:** The tool returns a redacted, structured failure and the pipeline checkpoints the stage as failed/blocked for user action.
- **Must not:** Retry a chargeable creation, switch provider/model, or continue to downstream generation.
- **Verification:** Mocked 403/quota response tests and secret-redaction assertions.
- **Priority:** Required.

## Verification Fixture

The checked-in fixture uses the passage supplied and corrected by the user:

> Before then, the only transport links between Mombasa, Kenya's main port, and Nairobi, Kenya's capital, were rough roads and an old railway line completed in 1901.

Expected treatment:

- One continuous English narration unit unless the measured narration exceeds the 10-second target and a grammar-safe split is required for planning.
- Two visual beats within one 10-second Wan clip or a single coherent camera move.
- Word-level English captions.
- No generated music.
- A short silence pad may be added after narration to reach exactly 10 seconds. If the natural take is longer, a bounded tempo adjustment of at most 1.15x may be used only after intelligibility review; words must never be trimmed.

## Verification Plan

| Criterion | Evidence | Status |
| --- | --- | --- |
| AC-001 | Schema and normalization tests | Pending |
| AC-002 | Coverage/boundary tests plus artifact review | Pending |
| AC-003 | Mocked Voice Design/TTS tests | Pending |
| AC-004 | Mocked image payload/download tests plus live artifact | Pending |
| AC-005 | Mocked async video tests plus live MP4 | Pending |
| AC-006 | Alignment/caption tests plus sampled frames | Pending |
| AC-007 | ffprobe report and manual playback | Pending |
| AC-008 | Regression suite and coverage report | Pending |
| AC-009 | Quota/error/redaction tests | Pending |

## Official References

- [Alibaba Cloud Model Studio image generation](https://help.aliyun.com/zh/model-studio/qwen-image-api)
- [Wan image-to-video API](https://help.aliyun.com/en/model-studio/legacy-image-to-video-api-reference/)
- [Qwen Voice Design API](https://help.aliyun.com/en/model-studio/voice-design-api-references)
- [Qwen TTS API](https://help.aliyun.com/en/model-studio/qwen-tts-api)
