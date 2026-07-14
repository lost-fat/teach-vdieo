---
name: dashscope
description: DashScope (Alibaba Cloud Model Studio / 阿里云百炼) integration for qwen3.7-plus text, qwen-image-2.0-pro images, qwen3-tts-vd Voice Design narration, wan2.6-i2v-flash video, and Qwen ASR. Use for the English textbook-to-video pipeline and other Beijing-region DashScope media workflows.
---

# DashScope

Requires `DASHSCOPE_API_KEY` in `.env`. Get one at https://dashscope.aliyun.com/.

## Current API

**CRITICAL:** DashScope's `/compatible-mode/v1/` is used only for chat completions here. Image generation, Voice Design/TTS, video, and ASR use **DashScope-native endpoints**.

All tools use `Authorization: Bearer $DASHSCOPE_API_KEY`. These URLs are for the China (Beijing) region; do not silently switch to an international endpoint.

### Text Generation

```text
POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
```

- Model: `qwen3.7-plus`
- OpenAI-compatible body: `{model, messages, temperature?, max_tokens?, response_format?}`
- Use `response_format: {type: "json_object"}` when downstream code requires machine-readable storyboards.

### Image Generation

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

- Model: `qwen-image-2.0-pro` (default), `qwen-image-max`, `wan2.7-image`, `z-image-turbo`
- Body: `{model, input: {messages: [{role: "user", content: [{text: "prompt"}]}]}, parameters: {size: "W*H", n, prompt_extend, watermark}}`
- **Size format uses asterisk:** `"1024*1024"` not `"1024x1024"`
- Response: `output.choices[0].message.content[0].image` (URL, valid ~24h) — must download separately

### Text-to-Speech

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

Same endpoint as image gen, different body.

- Models include `qwen3-tts-flash`, `qwen3-tts-instruct-flash`, `qwen3-tts-vd-2026-01-26`, and `qwen-tts-2025-05-22`.
- Body: `{model, input: {text, voice: "Cherry", language_type: "Auto"}}`
- Response: `output.audio.url` (WAV, valid ~24h) — must download separately

For `qwen3-tts-vd-2026-01-26`, a profile is a recipe, not a synthesis voice ID. Resolve it first:

```text
POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
```

- Create with model `qwen-voice-design`, action `create`, and `target_model: qwen3-tts-vd-2026-01-26`.
- Cache the returned account-specific voice ID by target model + profile + profile hash.
- Decode and persist `output.preview_audio.data` (base64) when a new voice is created.
- Never reuse a voice created for a realtime model with the non-realtime VD model.
- Keep `output.audio.url` in the runtime `ToolResult` so ASR can fetch it directly; do not persist that temporary URL in a release artifact.

### Image-to-Video

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
Header: X-DashScope-Async: enable
```

- Model: `wan2.6-i2v-flash`
- Requires `input.img_url` (public URL or supported local image encoded as a data URI).
- Duration: integer `2`-`15` seconds; Phase 1 validation uses exactly `10` seconds.
- Phase 1 defaults: `1080P`, `audio: false`, `prompt_extend: false`, `watermark: false`.
- The model's native audio default can consume more quota; set `audio: false` explicitly when narration is generated separately.
- Returns `output.task_id`; poll `GET /api/v1/tasks/{task_id}`, then download `output.video_url` before it expires.

### ASR with Word-Level Timestamps

```text
POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription
Header: X-DashScope-Async: enable
```

- Model: `qwen3-asr-flash-filetrans` (NOT `qwen3-asr-flash` — the sync version has no word timestamps)
- Body: `{model, input: {file_url: "https://public-url/audio.mp3"}, parameters: {enable_words: true, language: "en", channel_id: [0]}}`
- Returns `task_id` → poll `GET /api/v1/tasks/{task_id}` until `SUCCEEDED` → download `output.result.transcription_url` → JSON with `transcripts[].sentences[].words[]`
- Timestamps in `begin_time`/`end_time` are in **milliseconds** — the tool normalizes to seconds

## OpenMontage Usage

For a model-locked, zero-budget run such as `english-textbook`, resolve and
execute the exact registry tools (`dashscope_text`, `dashscope_tts`,
`dashscope_asr`, `dashscope_image`, `dashscope_video`) directly. A selector's
`preferred_provider` is a preference, not a no-fallback guarantee. If a
selector must be used, also set `allowed_providers: ["dashscope"]` and pass
every locked model and parameter explicitly.

### Image via selector

```python
from tools.graphics.image_selector import ImageSelector

result = ImageSelector().execute({
    "preferred_provider": "dashscope",
    "allowed_providers": ["dashscope"],
    "model": "qwen-image-2.0-pro",
    "prompt": "Cinematic editorial view of Mombasa transport history, no text.",
    "size": "2688*1536",
    "n": 1,
    "prompt_extend": False,
    "watermark": False,
    "output_path": "projects/my-video/assets/images/scene.png",
})
```

### TTS via selector

```python
from tools.audio.tts_selector import TTSSelector

result = TTSSelector().execute({
    "preferred_provider": "dashscope",
    "allowed_providers": ["dashscope"],
    "text": "Please listen and repeat after me.",
    "model_id": "qwen3-tts-vd-2026-01-26",
    "voice_profile": "english_teacher_female",
    "language_type": "English",
    "output_path": "projects/my-video/assets/audio/narration.wav",
})
```

### Video via selector

```python
from tools.video.video_selector import VideoSelector

result = VideoSelector().execute({
    "preferred_provider": "dashscope",
    "allowed_providers": ["dashscope"],
    "operation": "image_to_video",
    "model_name": "wan2.6-i2v-flash",
    "prompt": "A gentle camera push-in while the teacher opens the book.",
    "reference_image_path": "projects/my-video/assets/images/scene.png",
    "duration": 10,
    "resolution": "1080P",
    "audio": False,
    "prompt_extend": False,
    "watermark": False,
    "output_path": "projects/my-video/assets/video/scene.mp4",
    "task_state_path": "projects/my-video/state/wan-task.json",
})
```

### ASR directly (word timestamps for subtitles)

```python
from tools.analysis.dashscope_asr import DashscopeAsr

result = DashscopeAsr().execute({
    "audio_url": "https://example.com/narration.wav",
    "output_path": "projects/my-video/assets/audio/transcription.json",
    "task_state_path": "projects/my-video/state/asr-task.json",
})

# result.data["words"] is a flat list of {text, begin_time_seconds, end_time_seconds}
```

## Recommended Workflow

1. **Image:** Generate a sample first. Check `prompt_extend: true` (default) — DashScope rewrites your prompt for better results. Disable if you need literal prompt adherence.
2. **TTS:** Generate a 10-15 second sample before full narration. Approve the Voice Design profile and pacing before committing to full generation.
3. **Video:** Validate with one 10-second, silent `wan2.6-i2v-flash` clip. Save the task ID so polling can resume without submitting a duplicate generation.
4. **ASR:** Prefer the temporary `dashscope_tts` runtime `audio_url`; otherwise audio must be at a **publicly accessible URL**.
5. **Subtitles:** Build from `result.data["words"]` — each word has `begin_time_seconds` and `end_time_seconds`. Group words into caption phrases by language semantics, not fixed character count.

## Parameters

### Image (`dashscope_image`)
- `prompt` (required): text prompt
- `model`: default `qwen-image-2.0-pro`
- `size`: default `"1024*1024"` — **asterisk separator, not "x"**
- `n`: 1-6 images
- `negative_prompt`: things to avoid (max 500 chars)
- `prompt_extend`: default `true` — auto-rewrite prompt for better results
- `watermark`: default `false`
- `seed`: for reproducibility

### TTS (`dashscope_tts`)
- `text` (required): text to synthesize (max 600 chars for qwen3-tts-flash)
- `model`: default `qwen3-tts-flash`
- `voice`: default `"Cherry"` — other voices: `"Ethan"`, `"Chelsie"`, etc.
- `language_type`: default `"Auto"` — `"Chinese"`, `"English"`, `"Japanese"`, `"Korean"`
- `instructions`: natural language delivery instructions (only for `qwen3-tts-instruct-flash`)
- `voice_profile`: for VD models, a reusable profile such as `english_teacher_female`; the tool resolves and caches the actual voice ID
- `voice_preview_output_path`: project-local path for a newly created Voice Design preview

### Video (`dashscope_video`)
- `prompt` (required): motion/camera description
- `model`: `wan2.6-i2v-flash`
- `reference_image_url` or `reference_image_path`: required image input
- `duration`: integer 2-15; Phase 1 acceptance value is 10
- `resolution`: `720P` or `1080P`; Phase 1 default is `1080P`
- `audio`: explicitly `false` when using separate TTS
- `external_task_id`: resume polling an already-submitted task without another generation
- `task_state_path`: durable JSON state written before submission and immediately after the task ID is returned; keep it inside the project so an interrupted run can resume safely

### ASR (`dashscope_asr`)
- `audio_url` (required): **must be publicly accessible URL**
- `model`: `qwen3-asr-flash-filetrans` (only model that supports word timestamps)
- `language`: optional single language code; use `"en"` for the English textbook pipeline
- `enable_words`: default `true` — required for word-level timestamps
- `poll_interval_seconds`: default `5.0`
- `timeout_seconds`: default `300`
- `external_task_id`: resume the original ASR task without a new submission
- `task_state_path`: durable project-local ASR task state; use a different path from the Wan task state

## Troubleshooting

- **Zero-budget stop:** In the Model Studio console, enable “免费额度用完即停” for every selected model. Free quota availability alone does not guarantee a hard zero-spend cap.
- **`AllocationQuota.FreeTierOnly` (HTTP 403):** Treat it as terminal: do not retry and do not auto-fallback to a paid model. Ask the user to select another free-quota model.
- **Image size error:** Use `"W*H"` with asterisk, not `"WxH"`. Example: `"2048*2048"`.
- **TTS no audio URL:** Check `output.audio.url` — if empty, the model name or voice may be wrong.
- **Voice Design mismatch:** A voice ID is tied to its `target_model`. Create/cache a new voice for `qwen3-tts-vd-2026-01-26` instead of reusing a realtime-model voice.
- **Video duplicated after timeout:** Read the durable `task_state_path` and resume with its `task_id` as `external_task_id`; do not submit a second task just because local polling stopped.
- **ASR "file not accessible":** `audio_url` must be publicly reachable. DashScope servers fetch the file; local paths and auth-gated URLs don't work.
- **ASR poll timeout:** Increase `timeout_seconds` (default 300). Long audio files take longer to transcribe.
- **ASR no word timestamps:** Ensure `enable_words: true` and model is `qwen3-asr-flash-filetrans` (not the sync `qwen3-asr-flash`).
- **Auth error (401):** Verify `DASHSCOPE_API_KEY` is set. Use `Authorization: Bearer $KEY` header.

## Safety

Never print or write the API key to logs, metadata, patches, or project artifacts. `.env.example` should contain only empty variable names. The tool's `_safe_error()` method redacts the key from error messages.
