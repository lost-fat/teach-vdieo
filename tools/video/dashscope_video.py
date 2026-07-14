"""Wan 2.6 image-to-video through Alibaba Cloud Model Studio.

HTTP video synthesis is asynchronous: submit a task, poll it, then download
the 24-hour result URL.  ``external_task_id`` resumes an existing task without
submitting a duplicate quota-consuming generation.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from datetime import datetime, timezone
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
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class DashscopeVideo(BaseTool):
    name = "dashscope_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "dashscope"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.ASYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set DASHSCOPE_API_KEY to a China (Beijing) Alibaba Cloud Model "
        "Studio API key."
    )
    fallback_tools = ["kling_video", "veo_video", "wan_video"]
    agent_skills = ["dashscope", "ai-video-gen"]

    capabilities = ["image_to_video", "reference_image", "async_generation"]
    supports = {
        "text_to_video": False,
        "image_to_video": True,
        "reference_image": True,
        "native_audio": True,
        "duration_seconds": {"minimum": 2, "maximum": 15, "integer": True},
        "resolutions": ["720P", "1080P"],
    }
    best_for = [
        "2-15 second image-conditioned clips",
        "10-second English-textbook visual beats",
        "China (Beijing) Model Studio free-quota validation",
    ]
    not_good_for = ["text-only generation", "offline generation"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "maxLength": 1500},
            "operation": {
                "type": "string",
                "enum": ["image_to_video"],
                "default": "image_to_video",
            },
            "model": {
                "type": "string",
                "enum": ["wan2.6-i2v-flash"],
                "default": "wan2.6-i2v-flash",
            },
            "reference_image_url": {"type": "string"},
            "reference_image_path": {"type": "string"},
            "negative_prompt": {"type": "string", "maxLength": 500},
            "duration": {
                "type": "integer",
                "minimum": 2,
                "maximum": 15,
                "default": 10,
            },
            "resolution": {
                "type": "string",
                "enum": ["720P", "1080P"],
                "default": "1080P",
            },
            "audio": {
                "type": "boolean",
                "default": False,
                "description": "Keep false when narration is mixed separately.",
            },
            "audio_url": {"type": "string"},
            "prompt_extend": {"type": "boolean", "default": False},
            "shot_type": {
                "type": "string",
                "enum": ["single", "multi"],
                "default": "single",
            },
            "watermark": {"type": "boolean", "default": False},
            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
            "external_task_id": {"type": "string"},
            "task_state_path": {
                "type": "string",
                "description": (
                    "Durable JSON state written immediately after task submission "
                    "so polling can resume without another generation."
                ),
            },
            "poll_interval_seconds": {
                "type": "number",
                "minimum": 0,
                "default": 15,
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "default": 900,
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=3.0,
        retryable_errors=["rate_limit", "timeout", "server_error"],
    )
    resume_support = ResumeSupport.FROM_CHECKPOINT
    idempotency_key_fields = [
        "prompt",
        "model",
        "reference_image_url",
        "reference_image_path",
        "negative_prompt",
        "duration",
        "resolution",
        "audio",
        "audio_url",
        "prompt_extend",
        "shot_type",
        "watermark",
        "seed",
    ]
    side_effects = [
        "writes an MP4 file to output_path",
        "calls the DashScope asynchronous video generation API",
    ]
    user_visible_verification = [
        "Watch the clip for motion coherence and image fidelity",
        "Verify duration, dimensions, codec, and audio tracks with ffprobe",
    ]

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "video-generation/video-synthesis"
    )
    POLL_URL_TEMPLATE = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def get_status(self) -> ToolStatus:
        return (
            ToolStatus.AVAILABLE
            if os.environ.get("DASHSCOPE_API_KEY")
            else ToolStatus.UNAVAILABLE
        )

    def is_operation_available(self, operation: str) -> bool:
        return operation == "image_to_video"

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Return a conservative USD equivalent of the Beijing list price."""
        duration = self._duration(inputs.get("duration", 10))
        resolution = self._resolution(inputs.get("resolution", "1080P"))
        audio = bool(inputs.get("audio", False))
        cny_per_second = {
            ("720P", False): 0.15,
            ("720P", True): 0.30,
            ("1080P", False): 0.25,
            ("1080P", True): 0.50,
        }[(resolution, audio)]
        # Divide by a deliberately conservative fixed conversion assumption.
        return round(duration * cny_per_second / 7.0, 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 300.0

    @staticmethod
    def _duration(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("duration must be an integer from 2 through 15 seconds")
        try:
            numeric = float(value)
            duration = int(numeric)
        except (TypeError, ValueError) as exc:
            raise ValueError("duration must be an integer from 2 through 15 seconds") from exc
        if numeric != duration or not 2 <= duration <= 15:
            raise ValueError("duration must be an integer from 2 through 15 seconds")
        return duration

    @staticmethod
    def _resolution(value: Any) -> str:
        resolution = str(value or "1080P").upper()
        if resolution not in {"720P", "1080P"}:
            raise ValueError("resolution must be 720P or 1080P")
        return resolution

    @staticmethod
    def _image_data_uri(path_value: str) -> str:
        path = Path(path_value)
        if not path.is_file():
            raise ValueError(f"reference image not found: {path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("reference image exceeds the Wan 2.6 20 MB limit")
        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type not in {
            "image/jpeg",
            "image/png",
            "image/bmp",
            "image/webp",
        }:
            raise ValueError("reference image must be JPEG, PNG, BMP, or WEBP")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _resolve_image(self, inputs: dict[str, Any]) -> str:
        url = inputs.get("reference_image_url") or inputs.get("image_url")
        if url:
            return str(url)
        path = inputs.get("reference_image_path")
        if path:
            return self._image_data_uri(str(path))
        raise ValueError(
            "image_to_video requires reference_image_url or reference_image_path"
        )

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        model = str(inputs.get("model", "wan2.6-i2v-flash"))
        if model != "wan2.6-i2v-flash":
            raise ValueError("dashscope_video supports only wan2.6-i2v-flash")
        prompt = str(inputs.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")

        prompt_extend = bool(inputs.get("prompt_extend", False))
        input_data: dict[str, Any] = {
            "prompt": prompt,
            "img_url": self._resolve_image(inputs),
        }
        if inputs.get("negative_prompt"):
            input_data["negative_prompt"] = str(inputs["negative_prompt"])
        if inputs.get("audio_url") and bool(inputs.get("audio", False)):
            input_data["audio_url"] = str(inputs["audio_url"])

        parameters: dict[str, Any] = {
            "resolution": self._resolution(inputs.get("resolution", "1080P")),
            "duration": self._duration(inputs.get("duration", 10)),
            "prompt_extend": prompt_extend,
            "audio": bool(inputs.get("audio", False)),
            "watermark": bool(inputs.get("watermark", False)),
        }
        if inputs.get("seed") is not None:
            parameters["seed"] = int(inputs["seed"])
        # The API only applies shot_type when prompt rewriting is enabled.
        if prompt_extend and inputs.get("shot_type"):
            parameters["shot_type"] = str(inputs["shot_type"])

        return {"model": model, "input": input_data, "parameters": parameters}

    @staticmethod
    def _task_state_path(inputs: dict[str, Any]) -> Path:
        configured = inputs.get("task_state_path")
        if configured:
            return Path(str(configured))
        output_path = Path(str(inputs.get("output_path", "dashscope_video.mp4")))
        return output_path.with_suffix(output_path.suffix + ".task.json")

    @classmethod
    def _write_task_state(cls, path: Path, state: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "provider": "dashscope",
            "tool": cls.name,
            **state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @classmethod
    def _read_task_state(cls, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Existing DashScope video task state is unreadable; refusing "
                "a duplicate submission."
            ) from exc
        if (
            not isinstance(state, dict)
            or state.get("provider") != "dashscope"
            or state.get("tool") != cls.name
        ):
            raise RuntimeError(
                "Existing video task state is invalid; refusing a duplicate "
                "submission."
            )
        return state

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="DASHSCOPE_API_KEY not set. " + self.install_instructions,
            )

        import requests

        return self._execute_with_http(inputs, http=requests, api_key=api_key)

    def _execute_with_http(
        self,
        inputs: dict[str, Any],
        *,
        http: Any,
        api_key: str | None = None,
    ) -> ToolResult:
        started = time.time()
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            return ToolResult(success=False, error="DASHSCOPE_API_KEY not set.")

        model = str(inputs.get("model", "wan2.6-i2v-flash"))
        task_id = str(inputs.get("external_task_id") or "")
        external_task_id = task_id or None
        task_state_path = self._task_state_path(inputs)
        output_path_value = str(inputs.get("output_path", "dashscope_video.mp4"))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            if not task_id:
                existing_state = self._read_task_state(task_state_path)
                if existing_state is not None:
                    if existing_state.get("model") != model:
                        raise RuntimeError(
                            "Existing video task state targets a different model; "
                            "refusing a possible duplicate submission."
                        )
                    persisted_task_id = str(
                        existing_state.get("task_id") or ""
                    )
                    if persisted_task_id:
                        task_id = persisted_task_id
                        external_task_id = persisted_task_id
                    else:
                        # A prior process may have died after the remote POST
                        # returned but before its task ID was durably written.
                        # There is no safe way to prove that no quota was
                        # consumed, so a zero-budget run must fail closed.
                        raise RuntimeError(
                            "Existing pre-submit task state has no task ID; "
                            "refusing a possible duplicate submission."
                        )

            if not task_id:
                # Verify durable state is writable before consuming generation
                # quota.  Once the API returns a task ID, persist it before the
                # first poll so an interrupted process can resume safely.
                self._write_task_state(
                    task_state_path,
                    {
                        "model": model,
                        "task_id": None,
                        "status": "ready_to_submit",
                        "output_path": output_path_value,
                    },
                )
                payload = self._build_payload(inputs)
                submit = http.post(
                    self.SUBMIT_URL,
                    headers={**headers, "X-DashScope-Async": "enable"},
                    json=payload,
                    timeout=(10, 60),
                )
                submitted = ensure_success(submit)
                task_id = str(submitted.get("output", {}).get("task_id") or "")
                if not task_id:
                    return ToolResult(
                        success=False,
                        error="DashScope video submission returned no output.task_id",
                    )
                external_task_id = task_id
                self._write_task_state(
                    task_state_path,
                    {
                        "model": model,
                        "task_id": task_id,
                        "status": "submitted",
                        "output_path": output_path_value,
                    },
                )
            else:
                self._write_task_state(
                    task_state_path,
                    {
                        "model": model,
                        "task_id": task_id,
                        "status": "resuming",
                        "output_path": output_path_value,
                    },
                )

            result_data = self._poll(
                http=http,
                api_key=api_key,
                task_id=task_id,
                poll_interval=float(inputs.get("poll_interval_seconds", 15)),
                timeout_seconds=int(inputs.get("timeout_seconds", 900)),
            )
            output = result_data.get("output", {})
            video_url = output.get("video_url")
            if not video_url:
                return ToolResult(
                    success=False,
                    error="DashScope video task succeeded without output.video_url",
                    data={"task_id": task_id},
                )

            download = http.get(str(video_url), timeout=(10, 300))
            if int(getattr(download, "status_code", 0) or 0) >= 400:
                ensure_success(download)
            content = bytes(getattr(download, "content", b""))
            if not content:
                return ToolResult(
                    success=False,
                    error="DashScope video download returned an empty file",
                    data={"task_id": task_id},
                )

            output_path = Path(output_path_value)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path = output_path.with_suffix(output_path.suffix + ".part")
            partial_path.write_bytes(content)
            partial_path.replace(output_path)
            self._write_task_state(
                task_state_path,
                {
                    "model": model,
                    "task_id": task_id,
                    "status": "succeeded",
                    "output_path": str(output_path),
                },
            )

            from tools.video._shared import probe_output

            probed = probe_output(output_path)
            return ToolResult(
                success=True,
                data={
                    "provider": "dashscope",
                    "model": model,
                    "operation": "image_to_video",
                    "task_id": task_id,
                    "external_task_id": external_task_id,
                    "task_state_path": str(task_state_path),
                    "output": str(output_path),
                    "output_path": str(output_path),
                    "usage": result_data.get("usage", {}),
                    **probed,
                },
                artifacts=[str(output_path)],
                cost_usd=self.estimate_cost(inputs),
                duration_seconds=round(time.time() - started, 2),
                seed=inputs.get("seed"),
                model=model,
            )
        except DashscopeAPIError as exc:
            if task_id:
                try:
                    self._write_task_state(
                        task_state_path,
                        {
                            "model": model,
                            "task_id": task_id,
                            "status": "failed",
                            "error_code": exc.code,
                            "output_path": output_path_value,
                        },
                    )
                except OSError:
                    pass
            return ToolResult(
                success=False,
                error=str(exc),
                data={"task_id": task_id or None, **tool_error_data(exc)},
                duration_seconds=round(time.time() - started, 2),
                model=model,
            )
        except Exception as exc:
            if task_id:
                try:
                    self._write_task_state(
                        task_state_path,
                        {
                            "model": model,
                            "task_id": task_id,
                            "status": "interrupted",
                            "output_path": output_path_value,
                        },
                    )
                except OSError:
                    pass
            return ToolResult(
                success=False,
                error=f"DashScope video generation failed: {self._safe_error(exc)}",
                data={"task_id": task_id or None},
                duration_seconds=round(time.time() - started, 2),
                model=model,
            )

    def _poll(
        self,
        *,
        http: Any,
        api_key: str,
        task_id: str,
        poll_interval: float,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = http.get(
                self.POLL_URL_TEMPLATE.format(task_id=task_id),
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=(10, 60),
            )
            data = ensure_success(response)
            output = data.get("output", {})
            status = str(output.get("task_status", "")).upper()
            if status == "SUCCEEDED":
                return data
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                code = str(output.get("code") or status)
                message = str(output.get("message") or f"video task {status.lower()}")
                raise DashscopeAPIError(
                    http_status=(
                        403
                        if code == "AllocationQuota.FreeTierOnly"
                        else 400
                    ),
                    code=code,
                    message=message,
                    request_id=data.get("request_id"),
                )
            if poll_interval > 0:
                time.sleep(poll_interval)
        raise TimeoutError(
            f"DashScope video task {task_id} did not finish within {timeout_seconds}s"
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return safe_error_text(exc)
