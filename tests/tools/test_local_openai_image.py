"""Contract tests for the local OpenAI-compatible image connector."""

from __future__ import annotations

import base64

from tools.base_tool import ToolStatus
from tools.graphics.local_openai_image import LocalOpenAIImage


class _Response:
    def __init__(self, *, payload=None, content=b"", status_code=200, text=""):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_connector_posts_locked_flux_payload_and_prefers_base64(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_IMAGE_API_KEY", "test-secret")
    monkeypatch.setenv("LOCAL_IMAGE_BASE_URL", "http://image.local:8001")
    captured = {}
    image = b"generated-png"

    def fake_post(url, *, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response(payload={
            "data": [{
                "b64_json": base64.b64encode(image).decode(),
                "url": "http://image.local:8001/unneeded.png",
            }],
        })

    def fail_get(*args, **kwargs):
        raise AssertionError("URL download must not run when b64_json is available")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fail_get)
    output = tmp_path / "frame.png"

    result = LocalOpenAIImage().execute({
        "prompt": "男孩照顾一只受伤的小鸟。",
        "negative_prompt": "禁止文字。",
        "model": "flux2-klein-base-4b",
        "size": "1024x1024",
        "quality": "medium",
        "response_format": "url",
        "seed": 42,
        "output_path": str(output),
    })

    assert result.success is True
    assert output.read_bytes() == image
    assert captured["url"] == "http://image.local:8001/v1/images/generations"
    assert captured["json"] == {
        "model": "flux2-klein-base-4b",
        "prompt": "男孩照顾一只受伤的小鸟。\n\n避免以下内容：禁止文字。",
        "size": "1024x1024",
        "quality": "medium",
        "response_format": "url",
        "seed": 42,
    }
    assert captured["headers"]["Authorization"] == "Bearer test-secret"
    assert result.cost_usd == 0


def test_connector_downloads_relative_url_with_same_host_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_IMAGE_API_KEY", "test-secret")
    monkeypatch.setenv("LOCAL_IMAGE_BASE_URL", "http://image.local:8001")
    get_call = {}

    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: _Response(payload={"data": [{"url": "/files/frame.png"}]}),
    )

    def fake_get(url, *, headers, timeout):
        get_call.update({"url": url, "headers": headers, "timeout": timeout})
        return _Response(content=b"url-png")

    monkeypatch.setattr("requests.get", fake_get)
    output = tmp_path / "frame.png"

    result = LocalOpenAIImage().execute({"prompt": "小鸟", "output_path": str(output)})

    assert result.success is True
    assert output.read_bytes() == b"url-png"
    assert get_call == {
        "url": "http://image.local:8001/files/frame.png",
        "headers": {"Authorization": "Bearer test-secret"},
        "timeout": 120,
    }


def test_connector_requires_key(monkeypatch):
    monkeypatch.delenv("LOCAL_IMAGE_API_KEY", raising=False)

    tool = LocalOpenAIImage()
    result = tool.execute({"prompt": "小鸟"})

    assert tool.get_status() == ToolStatus.UNAVAILABLE
    assert result.success is False
    assert "LOCAL_IMAGE_API_KEY" in result.error
