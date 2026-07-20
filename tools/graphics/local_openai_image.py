"""Image generation through a local OpenAI-compatible HTTP endpoint."""

from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


DEFAULT_BASE_URL = "http://192.168.111.19:8001"
DEFAULT_MODEL = "flux2-klein-base-4b"


class LocalOpenAIImage(BaseTool):
    """Generate one image with the configured OpenAI-compatible service."""

    name = "local_openai_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "openai_compatible_local"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.API

    dependencies = ["requests"]
    install_instructions = (
        "Set LOCAL_IMAGE_API_KEY to the local image service API key.\n"
        f"Optionally set LOCAL_IMAGE_BASE_URL (default: {DEFAULT_BASE_URL})."
    )
    agent_skills: list[str] = []

    capabilities = ["generate_image", "generate_illustration", "text_to_image"]
    supports = {
        "seed": True,
        "url_response": True,
        "base64_response": True,
        "multiple_outputs": False,
    }
    best_for = ["local OpenAI-compatible image generation", "seeded lesson-video first frames"]
    not_good_for = ["offline generation", "multiple outputs in one request"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "negative_prompt": {"type": "string"},
            "model": {"type": "string", "default": DEFAULT_MODEL},
            "size": {"type": "string", "default": "1024x1024"},
            "quality": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
            },
            "response_format": {
                "type": "string",
                "enum": ["url", "b64_json"],
                "default": "url",
            },
            "seed": {"type": "integer"},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=1.5,
        retryable_errors=["rate_limit", "timeout", "server_error"],
    )
    idempotency_key_fields = ["prompt", "negative_prompt", "size", "quality", "model", "seed"]
    side_effects = ["writes image file to output_path", "calls configured local image API"]
    user_visible_verification = ["Inspect generated first frame for prompt relevance and continuity"]

    def get_status(self) -> ToolStatus:
        return (
            ToolStatus.AVAILABLE
            if os.environ.get("LOCAL_IMAGE_API_KEY")
            else ToolStatus.UNAVAILABLE
        )

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    @staticmethod
    def _safe_error(value: Any, api_key: str) -> str:
        text = str(value or "").strip()[:800]
        if api_key:
            text = text.replace(api_key, "[REDACTED]")
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)

    @staticmethod
    def _write_atomic(output_path: Path, content: bytes) -> None:
        if not content:
            raise ValueError("image response was empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, output_path)

    @staticmethod
    def _download_image(image_url: str, base_url: str, api_key: str) -> bytes:
        import requests

        resolved_url = urljoin(f"{base_url.rstrip('/')}/", image_url)
        headers: dict[str, str] = {}
        if urlparse(resolved_url).netloc == urlparse(base_url).netloc:
            headers["Authorization"] = f"Bearer {api_key}"
        response = requests.get(resolved_url, headers=headers, timeout=120)
        response.raise_for_status()
        return response.content

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("LOCAL_IMAGE_API_KEY", "")
        if not api_key:
            return ToolResult(
                success=False,
                error="LOCAL_IMAGE_API_KEY not set. " + self.install_instructions,
            )

        import requests

        start = time.time()
        base_url = os.environ.get("LOCAL_IMAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        endpoint = f"{base_url}/v1/images/generations"
        model = str(inputs.get("model") or DEFAULT_MODEL)
        prompt = str(inputs["prompt"]).strip()
        negative_prompt = str(inputs.get("negative_prompt") or "").strip()
        request_prompt = prompt
        if negative_prompt:
            request_prompt = f"{prompt}\n\n避免以下内容：{negative_prompt}"
        payload: dict[str, Any] = {
            "model": model,
            "prompt": request_prompt,
            "size": str(inputs.get("size") or "1024x1024"),
            "quality": str(inputs.get("quality") or "medium"),
            "response_format": str(inputs.get("response_format") or "url"),
        }
        if inputs.get("seed") is not None:
            payload["seed"] = int(inputs["seed"])

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        response = None
        try:
            for attempt in range(self.retry_policy.max_retries + 1):
                response = requests.post(endpoint, headers=headers, json=payload, timeout=300)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
                if attempt < self.retry_policy.max_retries:
                    time.sleep(self.retry_policy.backoff_seconds * (2**attempt))
            if response is None:
                raise RuntimeError("image service returned no response")
            if not response.ok:
                detail = self._safe_error(response.text, api_key)
                return ToolResult(
                    success=False,
                    error=f"本地图片服务返回 HTTP {response.status_code}: {detail}",
                )
            body = response.json()
            items = body.get("data") if isinstance(body, dict) else None
            if not isinstance(items, list) or not items or not isinstance(items[0], dict):
                return ToolResult(success=False, error="本地图片服务未返回图片数据。")

            item = items[0]
            content: bytes | None = None
            if item.get("b64_json"):
                try:
                    content = base64.b64decode(str(item["b64_json"]))
                except (ValueError, TypeError):
                    content = None
            if not content and item.get("url"):
                content = self._download_image(str(item["url"]), base_url, api_key)
            if not content:
                return ToolResult(success=False, error="图片返回缺少可用的 b64_json 或 url。")

            output_path = Path(str(inputs.get("output_path") or "local_openai_image.png"))
            self._write_atomic(output_path, content)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"本地图片生成失败：{self._safe_error(exc, api_key)}",
            )

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "prompt": prompt,
                "output": str(output_path),
                "outputs": [str(output_path)],
                "images_generated": 1,
            },
            artifacts=[str(output_path)],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            seed=payload.get("seed"),
            model=model,
        )
