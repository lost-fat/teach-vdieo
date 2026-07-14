"""Offline contracts for the DashScope Phase 1 provider path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools._dashscope.errors import DashscopeAPIError, ensure_success
from tools.audio.dashscope_tts import DashscopeTTS
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
    assert payload["messages"] == [
        {"role": "system", "content": "Return a source-faithful plan."},
        {"role": "user", "content": "Plan this passage."},
    ]


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
        "qwen3-tts-vd-2026-01-26", "english_teacher_female"
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

    voice, created = tool._resolve_voice(
        api_key="fake",
        inputs={
            "model": "qwen3-tts-vd-2026-01-26",
            "voice_profile": "english_teacher_female",
        },
        http=NoNetwork(),
    )
    assert voice == "cached-compatible-voice"
    assert created is False


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
