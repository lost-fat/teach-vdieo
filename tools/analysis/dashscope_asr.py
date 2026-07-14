"""DashScope (Alibaba Cloud Bailian) ASR with word-level timestamps.

Uses the DashScope-native async transcription endpoint with
X-DashScope-Async: enable header. The model qwen3-asr-flash-filetrans is the
ONLY DashScope path that returns word-level timestamps (the sync
qwen3-asr-flash via /chat/completions does not).

Pattern: submit (POST) -> poll (GET /tasks/{task_id}) -> download
transcription_url -> parse transcripts[].sentences[].words[].

This tool replaces the broken `whisperx` slot for subtitle-aligned
transcription. Word timestamps are normalized from milliseconds to seconds.
"""

from __future__ import annotations

import json
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


class DashscopeAsr(BaseTool):
    name = "dashscope_asr"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "analysis"
    provider = "dashscope"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.ASYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set DASHSCOPE_API_KEY to your Alibaba Cloud DashScope API key.\n"
        "  Get one at https://dashscope.aliyun.com/"
    )
    fallback = "transcriber"
    fallback_tools = ["transcriber"]
    agent_skills = ["dashscope"]

    capabilities = [
        "speech_to_text",
        "word_timestamps",
        "multilingual",
    ]
    supports = {
        "word_timestamps": True,
        "multilingual": True,
        "offline": False,
    }
    best_for = [
        "word-level timestamp transcription for subtitle alignment",
        "Mandarin and English speech recognition",
        "replacing whisperx when word-level granularity is needed",
    ]
    not_good_for = [
        "real-time transcription",
        "local/offline transcription",
    ]

    input_schema = {
        "type": "object",
        "required": ["audio_url"],
        "properties": {
            "audio_url": {
                "type": "string",
                "description": (
                    "Publicly accessible URL of the audio file to transcribe. "
                    "Must be reachable by DashScope servers — local paths "
                    "are not supported."
                ),
            },
            "model": {
                "type": "string",
                "enum": ["qwen3-asr-flash-filetrans"],
                "default": "qwen3-asr-flash-filetrans",
            },
            "language": {
                "type": "string",
                "enum": [
                    "zh", "yue", "en", "ja", "de", "ko", "ru", "fr",
                    "pt", "ar", "it", "es", "hi", "id", "th", "tr",
                    "uk", "vi", "cs", "da", "fil", "fi", "is", "ms",
                    "no", "pl", "sv",
                ],
                "description": (
                    "Single known audio language supported by the official "
                    "Filetrans API. Omit for automatic detection."
                ),
            },
            "enable_itn": {
                "type": "boolean",
                "default": False,
                "description": "Normalize spoken numbers into written form.",
            },
            "channel_id": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "default": [0],
                "maxItems": 1,
                "description": "Phase 1 processes one audio channel only.",
            },
            "enable_words": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Enable word-level timestamps. Required for subtitle "
                    "alignment."
                ),
            },
            "external_task_id": {
                "type": "string",
                "description": "Resume an existing DashScope ASR task ID.",
            },
            "task_state_path": {
                "type": "string",
                "description": (
                    "Project-local durable task state used to prevent duplicate "
                    "ASR submissions after interruption."
                ),
            },
            "output_path": {"type": "string"},
            "poll_interval_seconds": {
                "type": "number",
                "default": 5.0,
                "minimum": 1.0,
            },
            "timeout_seconds": {
                "type": "integer",
                "default": 300,
                "minimum": 30,
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=20, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=2.0,
        retryable_errors=["timeout", "rate_limit"],
    )
    resume_support = ResumeSupport.FROM_CHECKPOINT
    idempotency_key_fields = [
        "audio_url",
        "model",
        "enable_words",
        "language",
        "enable_itn",
        "channel_id",
    ]
    side_effects = [
        "writes transcription JSON to output_path",
        "calls DashScope (Alibaba Cloud) ASR API (async submit + poll)",
    ]
    user_visible_verification = [
        "Check transcription text for accuracy",
        "Verify word-level timestamps before building subtitles",
    ]

    SUBMIT_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/"
        "transcription"
    )
    POLL_URL_TEMPLATE = (
        "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    )

    def get_status(self) -> ToolStatus:
        if os.environ.get("DASHSCOPE_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # DashScope ASR pricing is per-minute; check console for actual cost.
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="DASHSCOPE_API_KEY not set. " + self.install_instructions,
            )

        audio_url = inputs.get("audio_url", "").strip()
        if not audio_url:
            return ToolResult(
                success=False, error="audio_url is required."
            )
        if not self._is_public_url(audio_url):
            return ToolResult(
                success=False,
                error=(
                    "audio_url must be a publicly accessible URL (http/https). "
                    "DashScope servers fetch the file; local paths are not "
                    "supported. Upload the audio to a public location first."
                ),
            )
        # DashScope ASR rejects http:// URLs with InvalidParameter.MalformedURL;
        # upgrade to https:// before submitting. Note: signed OSS URLs with
        # query params (Expires, Signature) may also be rejected — prefer clean
        # public file URLs when possible.
        if audio_url.startswith("http://"):
            audio_url = "https://" + audio_url[len("http://"):]
            inputs = {**inputs, "audio_url": audio_url}

        start = time.time()
        try:
            result = self._transcribe(inputs, api_key=api_key)
        except DashscopeAPIError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                data=tool_error_data(exc),
                duration_seconds=round(time.time() - start, 2),
                model=str(inputs.get("model", "qwen3-asr-flash-filetrans")),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"DashScope ASR failed: {self._safe_error(exc)}",
                duration_seconds=round(time.time() - start, 2),
                model=str(inputs.get("model", "qwen3-asr-flash-filetrans")),
            )

        result.duration_seconds = round(time.time() - start, 2)
        return result

    @staticmethod
    def _task_state_path(inputs: dict[str, Any]) -> Path:
        configured = inputs.get("task_state_path")
        if configured:
            return Path(str(configured))
        output_path = Path(str(inputs.get("output_path", "dashscope_asr.json")))
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
                "Existing DashScope ASR task state is unreadable; refusing a "
                "duplicate submission."
            ) from exc
        if (
            not isinstance(state, dict)
            or state.get("provider") != "dashscope"
            or state.get("tool") != cls.name
        ):
            raise RuntimeError(
                "Existing ASR task state is invalid; refusing a duplicate "
                "submission."
            )
        return state

    def _transcribe(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        requests_module: Any | None = None,
    ) -> ToolResult:
        if requests_module is None:
            import requests as requests_module

        payload = self._build_payload(inputs)
        model = str(payload["model"])
        output_path = Path(str(inputs.get("output_path", "dashscope_asr.json")))
        task_state_path = self._task_state_path(inputs)
        task_id = str(inputs.get("external_task_id") or "")
        external_task_id = task_id or None
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        try:
            if not task_id:
                existing_state = self._read_task_state(task_state_path)
                if existing_state is not None:
                    if existing_state.get("model") != model:
                        raise RuntimeError(
                            "Existing ASR task state targets a different model; "
                            "refusing a possible duplicate submission."
                        )
                    persisted_task_id = str(
                        existing_state.get("task_id") or ""
                    )
                    if persisted_task_id:
                        task_id = persisted_task_id
                        external_task_id = persisted_task_id
                    else:
                        raise RuntimeError(
                            "Existing pre-submit ASR task state has no task ID; "
                            "refusing a possible duplicate submission."
                        )

            if not task_id:
                self._write_task_state(
                    task_state_path,
                    {
                        "model": model,
                        "task_id": None,
                        "status": "ready_to_submit",
                        "output_path": str(output_path),
                    },
                )
                submit_resp = requests_module.post(
                    self.SUBMIT_URL,
                    headers=headers,
                    json=payload,
                    timeout=(10, 60),
                )
                submit_data = ensure_success(submit_resp)
                task_id = str(
                    submit_data.get("output", {}).get("task_id") or ""
                )
                if not task_id:
                    raise RuntimeError(
                        "DashScope ASR submit succeeded but did not return "
                        "output.task_id"
                    )
                external_task_id = task_id
                self._write_task_state(
                    task_state_path,
                    {
                        "model": model,
                        "task_id": task_id,
                        "status": "submitted",
                        "output_path": str(output_path),
                    },
                )
            else:
                self._write_task_state(
                    task_state_path,
                    {
                        "model": model,
                        "task_id": task_id,
                        "status": "resuming",
                        "output_path": str(output_path),
                    },
                )

            poll_data = self._poll_task(
                requests_module=requests_module,
                api_key=api_key,
                task_id=task_id,
                poll_interval=float(inputs.get("poll_interval_seconds", 5.0)),
                timeout_seconds=int(inputs.get("timeout_seconds", 300)),
            )

            # qwen3-asr-flash-filetrans returns output.result.transcription_url
            # (singular "result", NOT "results" like paraformer-v2).
            result = poll_data.get("output", {}).get("result", {})
            transcription_url = result.get("transcription_url")
            if not transcription_url:
                raise RuntimeError(
                    "DashScope ASR task succeeded but "
                    "result.transcription_url missing"
                )

            trans_resp = requests_module.get(transcription_url, timeout=120)
            if int(getattr(trans_resp, "status_code", 0) or 0) >= 400:
                ensure_success(trans_resp)
            transcription = trans_resp.json()
            if not isinstance(transcription, dict):
                raise RuntimeError(
                    "DashScope ASR transcription download was not a JSON object"
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            safe_transcription = self._sanitize_transcription_artifact(
                transcription
            )
            output_path.write_text(
                json.dumps(safe_transcription, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._write_task_state(
                task_state_path,
                {
                    "model": model,
                    "task_id": task_id,
                    "status": "succeeded",
                    "output_path": str(output_path),
                },
            )

            words = self._extract_words(safe_transcription)
            transcripts = safe_transcription.get("transcripts", [])
            return ToolResult(
                success=True,
                data={
                    "provider": "dashscope",
                    "model": model,
                    "audio_source": "temporary_url",
                    "task_id": task_id,
                    "external_task_id": external_task_id,
                    "task_state_path": str(task_state_path),
                    "transcripts": transcripts,
                    "words": words,
                    "word_count": len(words),
                    "output": str(output_path),
                },
                artifacts=[str(output_path)],
                cost_usd=self.estimate_cost(inputs),
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
                            "output_path": str(output_path),
                        },
                    )
                except OSError:
                    pass
            raise
        except Exception:
            if task_id:
                try:
                    self._write_task_state(
                        task_state_path,
                        {
                            "model": model,
                            "task_id": task_id,
                            "status": "interrupted",
                            "output_path": str(output_path),
                        },
                    )
                except OSError:
                    pass
            raise

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "enable_words": bool(inputs.get("enable_words", True)),
            "enable_itn": bool(inputs.get("enable_itn", False)),
            "channel_id": list(inputs.get("channel_id", [0])),
        }
        if inputs.get("language"):
            parameters["language"] = str(inputs["language"])
        return {
            "model": inputs.get(
                "model", "qwen3-asr-flash-filetrans"
            ),
            "input": {
                "file_url": inputs["audio_url"],
            },
            "parameters": parameters,
        }

    @classmethod
    def _sanitize_transcription_artifact(cls, value: Any) -> Any:
        """Remove temporary provider URLs before persisting ASR output."""

        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key).casefold()
                if lowered.endswith("url") or lowered.endswith("_url"):
                    continue
                sanitized[str(key)] = cls._sanitize_transcription_artifact(item)
            return sanitized
        if isinstance(value, list):
            return [cls._sanitize_transcription_artifact(item) for item in value]
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return "[redacted-url]"
        return value

    def _poll_task(
        self,
        *,
        requests_module: Any,
        api_key: str,
        task_id: str,
        poll_interval: float,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        headers = {"Authorization": f"Bearer {api_key}"}
        while time.time() < deadline:
            time.sleep(poll_interval)
            resp = requests_module.get(
                self.POLL_URL_TEMPLATE.format(task_id=task_id),
                headers=headers,
                timeout=(10, 60),
            )
            data = ensure_success(resp)
            output = data.get("output", {})
            status = str(output.get("task_status", "")).upper()
            if status == "SUCCEEDED":
                return data
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                code = str(output.get("code") or status)
                message = str(
                    output.get("message") or f"ASR task {status.lower()}"
                )
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
        raise TimeoutError(
            f"DashScope ASR task {task_id} did not finish within "
            f"{timeout_seconds}s"
        )

    @staticmethod
    def _is_public_url(url: str) -> bool:
        return url.startswith("http://") or url.startswith("https://")

    @staticmethod
    def _extract_words(
        transcription: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Extract flat word list with timestamps normalized to seconds."""
        words: list[dict[str, Any]] = []
        for transcript in transcription.get("transcripts", []):
            for sentence in transcript.get("sentences", []):
                for word in sentence.get("words", []):
                    words.append(
                        {
                            "text": word.get("text", ""),
                            "begin_time_seconds": round(
                                word.get("begin_time", 0) / 1000.0, 3
                            ),
                            "end_time_seconds": round(
                                word.get("end_time", 0) / 1000.0, 3
                            ),
                        }
                    )
        return words

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return safe_error_text(exc)
