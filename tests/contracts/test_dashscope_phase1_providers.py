"""Offline contracts for the DashScope Phase 1 provider path."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tools._dashscope.errors import DashscopeAPIError, ensure_success, safe_error_text
from tools.analysis.dashscope_asr import DashscopeAsr
from tools.audio.dashscope_tts import DashscopeTTS
from tools.graphics.dashscope_image import DashscopeImage
from tools.text.dashscope_text import DashscopeText
from tools.video.dashscope_video import DashscopeVideo


class FakeResponse:
    def __init__(self, status_code=200, data=None, content=b"data"):
        self.status_code = status_code
        self._data = data or {}
        self.content = content
        self.headers = {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


def test_free_tier_error_is_structured_terminal_and_redacted(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret-test-key")
    response = FakeResponse(
        403,
        {
            "code": "AllocationQuota.FreeTierOnly",
            "message": "quota exhausted for secret-test-key",
            "request_id": "req-1",
        },
    )

    with pytest.raises(DashscopeAPIError) as captured:
        ensure_success(response)

    error = captured.value
    assert error.quota_exhausted is True
    assert error.retryable is False
    assert error.request_id == "req-1"
    assert "secret-test-key" not in str(error)
    assert "[redacted]" in str(error)
    assert error.terminal is True
    assert error.terminal_reason == "free_tier_exhausted"


@pytest.mark.parametrize(
    ("status", "code", "message", "terminal", "retryable", "reason"),
    [
        (400, "Arrearage", "account has overdue payment", True, False, "billing"),
        (404, "ModelUnavailable", "selected model is unavailable", True, False, "model_unavailable"),
        (401, "InvalidApiKey", "invalid credential", True, False, "authorization"),
        (429, "Throttling.RateQuota", "try later", False, True, None),
    ],
)
def test_dashscope_error_classifies_terminal_and_retryable_failures(
    status, code, message, terminal, retryable, reason
):
    with pytest.raises(DashscopeAPIError) as captured:
        ensure_success(
            FakeResponse(status, {"code": code, "message": message})
        )

    error = captured.value
    assert error.terminal is terminal
    assert error.retryable is retryable
    assert error.terminal_reason == reason


@pytest.mark.parametrize(
    "sanitize",
    [
        safe_error_text,
        DashscopeAsr._safe_error,
        DashscopeTTS._safe_error,
        DashscopeImage._safe_error,
        DashscopeText._safe_error,
        DashscopeVideo._safe_error,
    ],
)
def test_signed_media_url_is_never_exposed_in_error_text(sanitize):
    signed_url = (
        "https://oss-cn-beijing.aliyuncs.com/media.wav?"
        "Expires=123&Signature=AUDIO-SECRET"
    )

    rendered = sanitize(ConnectionError(f"download failed for {signed_url}"))

    assert "AUDIO-SECRET" not in rendered
    assert "Signature=" not in rendered
    assert signed_url not in rendered
    assert "[redacted-url]" in rendered


def test_dashscope_text_payload_locks_json_and_model():
    payload = DashscopeText()._build_payload(
        {
            "system_prompt": "Return a source-faithful plan.",
            "prompt": "Plan this passage.",
            "model": "qwen3.7-plus",
        }
    )

    assert payload["model"] == "qwen3.7-plus"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"].startswith(
        "Return a source-faithful plan."
    )
    assert payload["messages"][1] == {
        "role": "user",
        "content": "Plan this passage.",
    }
    assert any(
        "json" in message["content"].casefold()
        for message in payload["messages"]
    )


def test_dashscope_text_defaults_to_confirmed_model():
    assert (
        DashscopeText().input_schema["properties"]["model"]["default"]
        == "qwen3.7-plus"
    )


def test_tts_supports_selected_voice_design_model():
    models = DashscopeTTS().input_schema["properties"]["model"]["enum"]
    assert "qwen3-tts-vd-2026-01-26" in models


def test_voice_design_payload_target_matches_synthesis_model():
    tool = DashscopeTTS()
    payload = tool._build_voice_design_payload(
        model="qwen3-tts-vd-2026-01-26",
        profile_id="english_teacher_female",
    )

    assert payload["model"] == "qwen-voice-design"
    assert payload["input"]["action"] == "create"
    assert payload["input"]["target_model"] == "qwen3-tts-vd-2026-01-26"
    assert payload["input"]["language"] == "en"
    assert "English teacher" in payload["input"]["voice_prompt"]


def test_voice_design_cache_key_separates_realtime_and_nonrealtime():
    tool = DashscopeTTS()
    new_key = tool._voice_cache_key(
        "qwen3-tts-vd-2026-01-26", "english_teacher_female"
    )
    old_key = tool._voice_cache_key(
        "qwen3-tts-vd-realtime-2026-01-15", "english_teacher_female"
    )
    assert new_key != old_key


def test_voice_design_cache_reuses_compatible_voice(tmp_path, monkeypatch):
    cache_path = tmp_path / "voices.json"
    monkeypatch.setenv("DASHSCOPE_VOICE_CACHE_FILE", str(cache_path))
    tool = DashscopeTTS()
    key = tool._voice_cache_key(
        "qwen3-tts-vd-2026-01-26",
        "english_teacher_female",
        api_key="fake",
    )
    cache_path.write_text(
        json.dumps(
            {
                key: {
                    "voice": "cached-compatible-voice",
                    "model": "qwen3-tts-vd-2026-01-26",
                    "profile_id": "english_teacher_female",
                }
            }
        ),
        encoding="utf-8",
    )

    class NoNetwork:
        def post(self, *args, **kwargs):  # pragma: no cover - failure guard
            raise AssertionError("cache hit must not create a voice")

    voice, created, preview_path = tool._resolve_voice(
        api_key="fake",
        inputs={
            "model": "qwen3-tts-vd-2026-01-26",
            "voice_profile": "english_teacher_female",
        },
        http=NoNetwork(),
    )
    assert voice == "cached-compatible-voice"
    assert created is False
    assert preview_path is None


def test_voice_design_cache_key_is_scoped_to_api_account():
    tool = DashscopeTTS()
    first = tool._voice_cache_key(
        "qwen3-tts-vd-2026-01-26",
        "english_teacher_female",
        api_key="account-one-key",
    )
    second = tool._voice_cache_key(
        "qwen3-tts-vd-2026-01-26",
        "english_teacher_female",
        api_key="account-two-key",
    )
    assert first != second
    assert "account-one-key" not in first
    assert "account-two-key" not in second


def test_voice_creation_survives_cache_write_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DASHSCOPE_VOICE_CACHE_FILE", str(tmp_path / "unwritable.json")
    )
    tool = DashscopeTTS()
    monkeypatch.setattr(
        tool,
        "_write_voice_cache",
        lambda cache: (_ for _ in ()).throw(OSError("read-only filesystem")),
    )

    class FakeHttp:
        def post(self, *args, **kwargs):
            return FakeResponse(200, {"output": {"voice": "usable-new-voice"}})

    voice, created, preview_path = tool._resolve_voice(
        api_key="fake-account-key",
        inputs={
            "model": "qwen3-tts-vd-2026-01-26",
            "voice_profile": "english_teacher_female",
        },
        http=FakeHttp(),
    )

    assert voice == "usable-new-voice"
    assert created is True
    assert preview_path is None


def test_voice_design_synthesis_payload_uses_resolved_voice_without_instructions():
    payload = DashscopeTTS()._build_payload(
        {
            "text": "Before then.",
            "model": "qwen3-tts-vd-2026-01-26",
            "voice": "resolved-voice",
            "language_type": "English",
            "instructions": "This must not leak to a non-instruct model.",
        }
    )
    assert payload["model"] == "qwen3-tts-vd-2026-01-26"
    assert payload["input"]["voice"] == "resolved-voice"
    assert "instructions" not in payload["input"]
    assert "optimize_instructions" not in payload["input"]


def test_wan_payload_is_exactly_ten_seconds_silent_1080p():
    payload = DashscopeVideo()._build_payload(
        {
            "prompt": "A cinematic train route from Mombasa to Nairobi.",
            "reference_image_url": "https://example.com/frame.png",
            "model": "wan2.6-i2v-flash",
            "duration": 10,
            "resolution": "1080P",
            "audio": False,
            "prompt_extend": False,
            "watermark": False,
        }
    )
    assert payload == {
        "model": "wan2.6-i2v-flash",
        "input": {
            "prompt": "A cinematic train route from Mombasa to Nairobi.",
            "img_url": "https://example.com/frame.png",
        },
        "parameters": {
            "resolution": "1080P",
            "duration": 10,
            "prompt_extend": False,
            "audio": False,
            "watermark": False,
        },
    }


@pytest.mark.parametrize("duration", [1, 16])
def test_wan_rejects_out_of_range_duration_instead_of_clamping(duration):
    with pytest.raises(ValueError, match="2.*15"):
        DashscopeVideo()._build_payload(
            {
                "prompt": "test",
                "reference_image_url": "https://example.com/frame.png",
                "duration": duration,
            }
        )


def test_wan_local_image_is_encoded_as_data_uri(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"stub")

    payload = DashscopeVideo()._build_payload(
        {
            "prompt": "test",
            "reference_image_path": str(image),
            "duration": 10,
        }
    )
    assert payload["input"]["img_url"].startswith("data:image/png;base64,")


def test_wan_external_task_id_resumes_without_submit(tmp_path, monkeypatch):
    tool = DashscopeVideo()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    calls = {"post": 0, "get": 0}

    class FakeHttp:
        def post(self, *args, **kwargs):
            calls["post"] += 1
            raise AssertionError("resume must not submit a duplicate task")

        def get(self, url, **kwargs):
            calls["get"] += 1
            if "/tasks/" in url:
                return FakeResponse(
                    200,
                    {
                        "output": {
                            "task_status": "SUCCEEDED",
                            "video_url": "https://example.com/video.mp4",
                        }
                    },
                )
            return FakeResponse(200, content=b"fake-mp4")

    result = tool._execute_with_http(
        {
            "prompt": "test",
            "external_task_id": "task-existing",
            "duration": 10,
            "output_path": str(tmp_path / "result.mp4"),
            "poll_interval_seconds": 0,
        },
        http=FakeHttp(),
    )

    assert result.success
    assert calls == {"post": 0, "get": 2}
    assert result.data["external_task_id"] == "task-existing"
    assert Path(result.artifacts[0]).read_bytes() == b"fake-mp4"


def test_dashscope_video_contract_is_discoverable(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    tool = DashscopeVideo()
    assert tool.provider == "dashscope"
    assert tool.capability == "video_generation"
    assert tool.supports["image_to_video"] is True
    assert tool.is_operation_available("image_to_video") is True
    assert tool.is_operation_available("text_to_video") is False


def test_dashscope_text_mocked_success_writes_structured_json(tmp_path):
    output_path = tmp_path / "plan.json"

    class FakeHttp:
        def post(self, *args, **kwargs):
            return FakeResponse(
                200,
                {
                    "choices": [
                        {"message": {"content": '{"visual_intent":"railway"}'}}
                    ],
                    "usage": {"total_tokens": 12},
                    "request_id": "req-text",
                },
            )

    result = DashscopeText()._execute_with_http(
        {
            "prompt": "Plan the passage.",
            "model": "qwen3.7-plus",
            "output_path": str(output_path),
        },
        api_key="fake-key",
        http=FakeHttp(),
    )

    assert result.success is True
    assert result.data["json"] == {"visual_intent": "railway"}
    assert json.loads(output_path.read_text(encoding="utf-8")) == result.data["json"]


def test_dashscope_text_quota_error_is_terminal():
    class FakeHttp:
        def post(self, *args, **kwargs):
            return FakeResponse(
                403,
                {
                    "code": "AllocationQuota.FreeTierOnly",
                    "message": "free quota exhausted",
                    "request_id": "req-text-quota",
                },
            )

    result = DashscopeText()._execute_with_http(
        {"prompt": "test"}, api_key="fake-key", http=FakeHttp()
    )

    assert result.success is False
    assert result.data["quota_exhausted"] is True
    assert result.data["retryable"] is False


def test_asr_execute_returns_structured_terminal_error(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    tool = DashscopeAsr()

    def fail(*args, **kwargs):
        raise DashscopeAPIError(
            http_status=403,
            code="AllocationQuota.FreeTierOnly",
            message="free quota exhausted",
        )

    monkeypatch.setattr(tool, "_transcribe", fail)
    result = tool.execute({"audio_url": "https://example.com/audio.wav"})

    assert result.success is False
    assert result.data["quota_exhausted"] is True
    assert result.data["terminal"] is True
    assert result.data["retryable"] is False


def test_asr_poll_preserves_nested_free_tier_error_code():
    class FakeRequests:
        @staticmethod
        def get(*args, **kwargs):
            return FakeResponse(
                200,
                {
                    "output": {
                        "task_status": "FAILED",
                        "code": "AllocationQuota.FreeTierOnly",
                        "message": "free quota exhausted",
                    },
                    "request_id": "req-asr-quota",
                },
            )

    with pytest.raises(DashscopeAPIError) as captured:
        DashscopeAsr()._poll_task(
            requests_module=FakeRequests(),
            api_key="fake-key",
            task_id="task-asr",
            poll_interval=0,
            timeout_seconds=1,
        )

    assert captured.value.code == "AllocationQuota.FreeTierOnly"
    assert captured.value.quota_exhausted is True


def test_asr_automatically_resumes_task_state_without_resubmitting(tmp_path):
    task_state_path = tmp_path / "asr-task.json"
    output_path = tmp_path / "raw-asr.json"
    task_state_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "provider": "dashscope",
                "tool": "dashscope_asr",
                "model": "qwen3-asr-flash-filetrans",
                "task_id": "task-asr-existing",
                "status": "submitted",
                "output_path": str(output_path),
            }
        ),
        encoding="utf-8",
    )
    calls = {"post": 0, "poll": 0, "download": 0}

    class FakeHttp:
        def post(self, *args, **kwargs):  # pragma: no cover - safety guard
            calls["post"] += 1
            raise AssertionError("a persisted ASR task must never be resubmitted")

        def get(self, url, **kwargs):
            if "/tasks/" in url:
                calls["poll"] += 1
                return FakeResponse(
                    200,
                    {
                        "output": {
                            "task_status": "SUCCEEDED",
                            "result": {
                                "transcription_url": (
                                    "https://oss.example/asr.json?Signature=SECRET"
                                )
                            },
                        }
                    },
                )
            calls["download"] += 1
            return FakeResponse(
                200,
                {
                    "file_url": (
                        "https://oss.example/audio.wav?Signature=AUDIO-SECRET"
                    ),
                    "transcripts": [
                        {
                            "sentences": [
                                {
                                    "words": [
                                        {
                                            "text": "Before",
                                            "begin_time": 0,
                                            "end_time": 400,
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                },
            )

    result = DashscopeAsr()._transcribe(
        {
            "audio_url": "https://example.com/audio.wav",
            "task_state_path": str(task_state_path),
            "output_path": str(output_path),
            "poll_interval_seconds": 0,
        },
        api_key="fake-key",
        requests_module=FakeHttp(),
    )

    assert result.success is True
    assert calls == {"post": 0, "poll": 1, "download": 1}
    assert result.data["task_id"] == "task-asr-existing"
    assert "audio_url" not in result.data
    assert "transcription_url" not in json.dumps(result.data)
    persisted = output_path.read_text(encoding="utf-8")
    assert "AUDIO-SECRET" not in persisted
    assert "Signature=" not in persisted
    assert "file_url" not in persisted
    state = json.loads(task_state_path.read_text(encoding="utf-8"))
    assert state["status"] == "succeeded"
    assert "transcription_url" not in state


def test_asr_refuses_to_submit_over_ambiguous_pre_submit_state(tmp_path):
    task_state_path = tmp_path / "asr-task.json"
    task_state_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "provider": "dashscope",
                "tool": "dashscope_asr",
                "model": "qwen3-asr-flash-filetrans",
                "task_id": None,
                "status": "ready_to_submit",
            }
        ),
        encoding="utf-8",
    )

    class NoNetwork:
        def post(self, *args, **kwargs):  # pragma: no cover - safety guard
            raise AssertionError("ambiguous ASR state must fail closed")

    with pytest.raises(RuntimeError, match="refusing.*duplicate"):
        DashscopeAsr()._transcribe(
            {
                "audio_url": "https://example.com/audio.wav",
                "task_state_path": str(task_state_path),
                "output_path": str(tmp_path / "raw-asr.json"),
            },
            api_key="fake-key",
            requests_module=NoNetwork(),
        )


def test_voice_design_mocked_create_synthesize_and_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "voices.json"
    output_path = tmp_path / "narration.wav"
    preview_path = tmp_path / "voice-preview.wav"
    monkeypatch.setenv("DASHSCOPE_VOICE_CACHE_FILE", str(cache_path))
    calls: list[str] = []

    class FakeHttp:
        def post(self, url, **kwargs):
            calls.append(url)
            if url == DashscopeTTS.VOICE_DESIGN_ENDPOINT:
                return FakeResponse(
                    200,
                    {
                        "output": {
                            "voice": "voice-created",
                            "preview_audio": {
                                "data": base64.b64encode(
                                    b"RIFF-voice-preview"
                                ).decode("ascii"),
                                "sample_rate": 24000,
                                "response_format": "wav",
                            },
                        },
                        "request_id": "voice-1",
                    },
                )
            return FakeResponse(
                200,
                {
                    "output": {"audio": {"url": "https://example.com/tts.wav"}},
                    "usage": {"characters": 12},
                    "request_id": "tts-1",
                },
            )

        def get(self, url, **kwargs):
            return FakeResponse(200, content=b"RIFF-fake-wave")

    result = DashscopeTTS()._execute_with_http(
        {
            "text": "Before then.",
            "model": "qwen3-tts-vd-2026-01-26",
            "voice_profile": "english_teacher_female",
            "language_type": "English",
            "output_path": str(output_path),
            "voice_preview_output_path": str(preview_path),
        },
        api_key="fake-key",
        http=FakeHttp(),
    )

    assert result.success is True
    assert result.data["voice"] == "voice-created"
    assert result.data["voice_created"] is True
    assert result.data["voice_preview_path"] == str(preview_path)
    assert result.data["audio_url"] == "https://example.com/tts.wav"
    assert output_path.read_bytes() == b"RIFF-fake-wave"
    assert preview_path.read_bytes() == b"RIFF-voice-preview"
    assert str(preview_path) in result.artifacts
    assert cache_path.exists()
    assert calls == [DashscopeTTS.VOICE_DESIGN_ENDPOINT, DashscopeTTS.ENDPOINT]


def test_wan_mocked_submit_returns_resumable_task_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    calls = {"post": 0, "poll": 0, "download": 0}

    class FakeHttp:
        def post(self, *args, **kwargs):
            calls["post"] += 1
            return FakeResponse(200, {"output": {"task_id": "task-new"}})

        def get(self, url, **kwargs):
            if "/tasks/" in url:
                calls["poll"] += 1
                return FakeResponse(
                    200,
                    {
                        "output": {
                            "task_status": "SUCCEEDED",
                            "video_url": "https://example.com/video.mp4",
                        }
                    },
                )
            calls["download"] += 1
            return FakeResponse(200, content=b"fake-mp4")

    task_state_path = tmp_path / "wan-task.json"
    result = DashscopeVideo()._execute_with_http(
        {
            "prompt": "A historic railway journey.",
            "reference_image_url": "https://example.com/frame.png",
            "duration": 10,
            "output_path": str(tmp_path / "result.mp4"),
            "task_state_path": str(task_state_path),
            "poll_interval_seconds": 0,
        },
        http=FakeHttp(),
    )

    assert result.success is True
    assert calls == {"post": 1, "poll": 1, "download": 1}
    assert result.data["task_id"] == "task-new"
    assert result.data["external_task_id"] == "task-new"
    task_state = json.loads(task_state_path.read_text(encoding="utf-8"))
    assert task_state["task_id"] == "task-new"
    assert task_state["status"] == "succeeded"
    assert "video_url" not in task_state


def test_wan_automatically_resumes_task_state_without_resubmitting(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    task_state_path = tmp_path / "wan-task.json"
    task_state_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "provider": "dashscope",
                "tool": "dashscope_video",
                "model": "wan2.6-i2v-flash",
                "task_id": "task-existing",
                "status": "submitted",
                "output_path": str(tmp_path / "result.mp4"),
            }
        ),
        encoding="utf-8",
    )
    calls = {"post": 0, "poll": 0, "download": 0}

    class FakeHttp:
        def post(self, *args, **kwargs):  # pragma: no cover - safety guard
            calls["post"] += 1
            raise AssertionError("a persisted task must never be resubmitted")

        def get(self, url, **kwargs):
            if "/tasks/" in url:
                calls["poll"] += 1
                return FakeResponse(
                    200,
                    {
                        "output": {
                            "task_status": "SUCCEEDED",
                            "video_url": "https://example.com/video.mp4",
                        }
                    },
                )
            calls["download"] += 1
            return FakeResponse(200, content=b"fake-mp4")

    result = DashscopeVideo()._execute_with_http(
        {
            "prompt": "A historic railway journey.",
            "reference_image_url": "https://example.com/frame.png",
            "output_path": str(tmp_path / "result.mp4"),
            "task_state_path": str(task_state_path),
            "poll_interval_seconds": 0,
        },
        http=FakeHttp(),
    )

    assert result.success is True
    assert calls == {"post": 0, "poll": 1, "download": 1}
    assert result.data["task_id"] == "task-existing"


def test_wan_refuses_to_submit_over_ambiguous_pre_submit_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    task_state_path = tmp_path / "wan-task.json"
    task_state_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "provider": "dashscope",
                "tool": "dashscope_video",
                "model": "wan2.6-i2v-flash",
                "task_id": None,
                "status": "ready_to_submit",
            }
        ),
        encoding="utf-8",
    )

    class NoNetwork:
        def post(self, *args, **kwargs):  # pragma: no cover - safety guard
            raise AssertionError("ambiguous state must fail closed")

    result = DashscopeVideo()._execute_with_http(
        {
            "prompt": "A historic railway journey.",
            "reference_image_url": "https://example.com/frame.png",
            "output_path": str(tmp_path / "result.mp4"),
            "task_state_path": str(task_state_path),
        },
        http=NoNetwork(),
    )

    assert result.success is False
    assert "refusing" in result.error.lower()
    assert "duplicate" in result.error.lower()


def test_wan_poll_free_tier_failure_is_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")

    class FakeHttp:
        def get(self, *args, **kwargs):
            return FakeResponse(
                200,
                {
                    "output": {
                        "task_status": "FAILED",
                        "code": "AllocationQuota.FreeTierOnly",
                        "message": "free quota exhausted",
                    },
                    "request_id": "req-video-quota",
                },
            )

    result = DashscopeVideo()._execute_with_http(
        {
            "prompt": "test",
            "external_task_id": "task-quota",
            "output_path": str(tmp_path / "quota.mp4"),
            "poll_interval_seconds": 0,
        },
        http=FakeHttp(),
    )

    assert result.success is False
    assert result.data["task_id"] == "task-quota"
    assert result.data["quota_exhausted"] is True
    assert result.data["retryable"] is False


def test_wan_poll_model_unavailable_is_structured_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")

    class FakeHttp:
        def get(self, *args, **kwargs):
            return FakeResponse(
                200,
                {
                    "output": {
                        "task_status": "FAILED",
                        "code": "ModelUnavailable",
                        "message": "selected model unavailable",
                    },
                    "request_id": "req-video-model",
                },
            )

    result = DashscopeVideo()._execute_with_http(
        {
            "prompt": "test",
            "external_task_id": "task-model",
            "output_path": str(tmp_path / "model.mp4"),
            "poll_interval_seconds": 0,
        },
        http=FakeHttp(),
    )

    assert result.success is False
    assert result.data["error_code"] == "ModelUnavailable"
    assert result.data["terminal"] is True
    assert result.data["terminal_reason"] == "model_unavailable"
    assert result.data["retryable"] is False
