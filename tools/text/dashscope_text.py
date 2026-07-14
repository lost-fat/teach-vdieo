"""Structured text generation through Alibaba Cloud Model Studio.

The English-textbook pipeline uses this provider for semantic segmentation
and visual planning.  Responses are locked to JSON so downstream artifact
validation never has to scrape prose or Markdown fences.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from tools._dashscope.errors import (
    DashscopeAPIError,
    ensure_success,
    safe_error_text,
    tool_error_data,
)
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


class DashscopeText(BaseTool):
    name = "dashscope_text"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "text_generation"
    provider = "dashscope"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set DASHSCOPE_API_KEY to a China (Beijing) Alibaba Cloud Model "
        "Studio API key."
    )
    agent_skills = ["dashscope"]

    capabilities = ["structured_text_generation", "json_output", "multilingual"]
    supports = {
        "json_output": True,
        "multilingual": True,
        "offline": False,
    }
    best_for = [
        "source-faithful semantic segmentation",
        "structured lesson and visual planning",
        "Chinese and English prompt understanding",
    ]
    not_good_for = ["offline generation", "unstructured creative prose"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "system_prompt": {
                "type": "string",
                "default": "Return valid JSON.",
            },
            "model": {
                "type": "string",
                "enum": ["qwen3.7-plus"],
                "default": "qwen3.7-plus",
            },
            "temperature": {
                "type": "number",
                "minimum": 0,
                "maximum": 2,
                "default": 0.2,
            },
            "max_tokens": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8192,
                "default": 4096,
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=2.0,
        retryable_errors=["rate_limit", "timeout", "server_error"],
    )
    idempotency_key_fields = [
        "system_prompt",
        "prompt",
        "model",
        "temperature",
        "max_tokens",
    ]
    side_effects = [
        "optionally writes structured JSON to output_path",
        "calls the DashScope text generation API",
    ]
    user_visible_verification = [
        "Validate the returned JSON against the stage artifact schema"
    ]

    ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def get_status(self) -> ToolStatus:
        return (
            ToolStatus.AVAILABLE
            if os.environ.get("DASHSCOPE_API_KEY")
            else ToolStatus.UNAVAILABLE
        )

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Deliberately conservative list-price estimate.  Free quota may make
        # the actual charge zero, but that must not be assumed by governance.
        approx_input_tokens = max(1, len(str(inputs.get("prompt", ""))) // 4)
        max_output_tokens = int(inputs.get("max_tokens", 4096))
        return round(approx_input_tokens * 0.000003 + max_output_tokens * 0.00001, 4)

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        system_prompt = str(
            inputs.get("system_prompt", "Return valid JSON.")
        )
        user_prompt = str(inputs["prompt"])
        if "json" not in f"{system_prompt}\n{user_prompt}".casefold():
            system_prompt = (
                f"{system_prompt.rstrip()} Return only one valid JSON object."
            )
        return {
            "model": inputs.get("model", "qwen3.7-plus"),
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": float(inputs.get("temperature", 0.2)),
            "max_tokens": int(inputs.get("max_tokens", 4096)),
            "response_format": {"type": "json_object"},
            # Qwen3.7 defaults to thinking mode, which is incompatible with
            # OpenAI-compatible JSON mode.  Keep this deterministic contract
            # explicit instead of relying on a provider-side default.
            "enable_thinking": False,
        }

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="DASHSCOPE_API_KEY not set. " + self.install_instructions,
            )

        import requests

        return self._execute_with_http(inputs, api_key=api_key, http=requests)

    def _execute_with_http(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        http: Any,
    ) -> ToolResult:
        started = time.time()
        payload = self._build_payload(inputs)
        try:
            response = http.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=(10, 120),
            )
            data = ensure_success(response)
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                return ToolResult(
                    success=False,
                    error="DashScope text generation returned no message content",
                )
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                return ToolResult(
                    success=False,
                    error=f"DashScope text generation returned invalid JSON: {exc}",
                )

            artifacts: list[str] = []
            output_path_value = inputs.get("output_path")
            if output_path_value:
                output_path = Path(str(output_path_value))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                artifacts.append(str(output_path))

            return ToolResult(
                success=True,
                data={
                    "provider": "dashscope",
                    "model": payload["model"],
                    "content": content,
                    "json": parsed,
                    "usage": data.get("usage", {}),
                    "request_id": data.get("request_id"),
                    "output": artifacts[0] if artifacts else None,
                },
                artifacts=artifacts,
                cost_usd=self.estimate_cost(inputs),
                duration_seconds=round(time.time() - started, 2),
                model=payload["model"],
            )
        except DashscopeAPIError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                data=tool_error_data(exc),
                duration_seconds=round(time.time() - started, 2),
                model=payload["model"],
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"DashScope text generation failed: {self._safe_error(exc)}",
                duration_seconds=round(time.time() - started, 2),
                model=payload["model"],
            )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return safe_error_text(exc)
