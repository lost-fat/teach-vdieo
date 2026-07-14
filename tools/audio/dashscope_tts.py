"""DashScope text-to-speech, including Qwen3 Voice Design profiles."""

from __future__ import annotations

import base64
import hashlib
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
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


VOICE_DESIGN_MODEL = "qwen3-tts-vd-2026-01-26"
DEFAULT_VOICE_PROFILE = "english_teacher_female"

VOICE_DESIGN_PROFILES: dict[str, dict[str, str]] = {
    "english_teacher_female": {
        "preferred_name": "om_eng_f_teacher",
        "preview_text": (
            "In 2017, the new railway was opened. Please listen and repeat "
            "after me."
        ),
        "voice_prompt": (
            "A warm adult female English teacher voice with clear pronunciation, "
            "a neutral international English accent, calm friendly tone, "
            "medium-low pitch, a brisk but clear classroom pace around 180 "
            "words per minute, and precise articulation, suitable for concise "
            "ESL textbook narration."
        ),
    },
    "english_teacher_male": {
        "preferred_name": "om_eng_m_teacher",
        "preview_text": (
            "Today we will learn an English sentence through a clear story scene."
        ),
        "voice_prompt": (
            "A composed adult male English teacher voice, warm and steady, with "
            "a neutral international accent, medium pitch, patient classroom "
            "delivery at a brisk but clear pace around 180 words per minute, "
            "and precise articulation."
        ),
    },
}


class DashscopeTTS(BaseTool):
    name = "dashscope_tts"
    version = "0.2.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "dashscope"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set DASHSCOPE_API_KEY to your Alibaba Cloud DashScope API key.\n"
        "  Get one at https://dashscope.aliyun.com/"
    )
    fallback = "piper_tts"
    fallback_tools = [
        "doubao_tts",
        "elevenlabs_tts",
        "openai_tts",
        "piper_tts",
    ]
    agent_skills = ["dashscope"]

    capabilities = [
        "text_to_speech",
        "voice_selection",
        "voice_design",
        "multilingual",
    ]
    supports = {
        "voice_cloning": False,
        "voice_design": True,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
    }
    best_for = [
        "natural Mandarin and multilingual narration via Qwen-TTS",
        "reusable Voice Design profiles for English textbook narration",
        "cost-effective TTS via Alibaba Cloud",
    ]
    not_good_for = ["fully offline production", "voice clone matching"]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to convert to speech.",
            },
            "model": {
                "type": "string",
                "enum": [
                    "qwen3-tts-flash",
                    "qwen3-tts-instruct-flash",
                    VOICE_DESIGN_MODEL,
                    "qwen-tts-2025-05-22",
                ],
                "default": "qwen3-tts-flash",
            },
            "voice": {
                "type": "string",
                "default": "Cherry",
                "description": "Built-in or account-specific DashScope voice ID.",
            },
            "voice_profile": {
                "type": "string",
                "enum": sorted(VOICE_DESIGN_PROFILES),
                "default": DEFAULT_VOICE_PROFILE,
                "description": (
                    "Reusable profile resolved to an account-specific Voice Design "
                    "ID when using qwen3-tts-vd-2026-01-26."
                ),
            },
            "language_type": {
                "type": "string",
                "default": "Auto",
                "enum": [
                    "Auto",
                    "Chinese",
                    "English",
                    "German",
                    "Italian",
                    "Portuguese",
                    "Spanish",
                    "Japanese",
                    "Korean",
                    "French",
                    "Russian",
                ],
            },
            "instructions": {
                "type": "string",
                "description": "Delivery instructions for instruct models only.",
            },
            "output_path": {"type": "string"},
            "voice_preview_output_path": {
                "type": "string",
                "description": (
                    "Where to persist the base64 Voice Design preview WAV. "
                    "Used only when a new Voice Design voice is created."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(
        max_retries=2, retryable_errors=["rate_limit", "timeout", "server_error"]
    )
    idempotency_key_fields = [
        "text",
        "voice",
        "voice_profile",
        "model",
        "language_type",
        "instructions",
    ]
    side_effects = [
        "writes audio file to output_path",
        "may create and cache an account-specific DashScope Voice Design ID",
        "calls DashScope (Alibaba Cloud) TTS API",
    ]
    user_visible_verification = ["Listen to generated audio for naturalness and pacing"]

    ENDPOINT = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    VOICE_DESIGN_ENDPOINT = (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    )

    def get_status(self) -> ToolStatus:
        return (
            ToolStatus.AVAILABLE
            if os.environ.get("DASHSCOPE_API_KEY")
            else ToolStatus.UNAVAILABLE
        )

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Conservative per-character estimate; free quota may make actual cost 0.
        return round(len(str(inputs.get("text", ""))) * 0.000015, 4)

    @staticmethod
    def _is_voice_design_model(model: str) -> bool:
        return model.startswith("qwen3-tts-vd-")

    @staticmethod
    def _profile_hash(profile: dict[str, str]) -> str:
        raw = json.dumps(profile, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _account_scope(api_key: str) -> str:
        """Return a non-reversible cache namespace for an API account key."""

        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]

    def _voice_cache_key(
        self,
        model: str,
        profile_id: str,
        *,
        api_key: str | None = None,
    ) -> str:
        profile = VOICE_DESIGN_PROFILES.get(profile_id)
        if profile is None:
            raise ValueError(f"Unknown DashScope voice profile: {profile_id}")
        resolved_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        account_scope = (
            self._account_scope(resolved_key) if resolved_key else "unscoped"
        )
        return (
            f"{account_scope}:{model}:{profile_id}:"
            f"{self._profile_hash(profile)}"
        )

    @staticmethod
    def _voice_cache_path() -> Path:
        configured = os.environ.get("DASHSCOPE_VOICE_CACHE_FILE")
        if configured:
            return Path(configured).expanduser()
        cache_root = Path(
            os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        )
        return cache_root / "openmontage" / "dashscope-voices.json"

    def _read_voice_cache(self) -> dict[str, Any]:
        path = self._voice_cache_path()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_voice_cache(self, cache: dict[str, Any]) -> None:
        path = self._voice_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _build_voice_design_payload(
        self,
        *,
        model: str,
        profile_id: str,
    ) -> dict[str, Any]:
        if not self._is_voice_design_model(model):
            raise ValueError("Voice Design target_model must be a qwen3-tts-vd model")
        profile = VOICE_DESIGN_PROFILES.get(profile_id)
        if profile is None:
            raise ValueError(f"Unknown DashScope voice profile: {profile_id}")
        return {
            "model": "qwen-voice-design",
            "input": {
                "action": "create",
                "target_model": model,
                "preferred_name": profile["preferred_name"],
                "voice_prompt": profile["voice_prompt"],
                "preview_text": profile["preview_text"],
                "language": "en",
            },
            "parameters": {"sample_rate": 24000, "response_format": "wav"},
        }

    def _resolve_voice(
        self,
        *,
        api_key: str,
        inputs: dict[str, Any],
        http: Any,
    ) -> tuple[str, bool, str | None]:
        model = str(inputs.get("model", "qwen3-tts-flash"))
        explicit_voice = inputs.get("voice")
        if not self._is_voice_design_model(model):
            return str(explicit_voice or "Cherry"), False, None

        # A real voice ID always wins over a profile.  Profile names are not
        # valid synthesis voice IDs, so resolve those through the cache.
        if explicit_voice and str(explicit_voice) not in VOICE_DESIGN_PROFILES:
            return str(explicit_voice), False, None
        profile_id = str(
            inputs.get("voice_profile")
            or explicit_voice
            or DEFAULT_VOICE_PROFILE
        )
        account_scope = self._account_scope(api_key)
        key = self._voice_cache_key(model, profile_id, api_key=api_key)
        cache = self._read_voice_cache()
        record = cache.get(key, {})
        if (
            isinstance(record, dict)
            and record.get("voice")
            and record.get("model") == model
            and record.get("profile_id") == profile_id
            and record.get("account_scope", account_scope) == account_scope
        ):
            cached_preview = record.get("preview_path")
            preview_path = (
                Path(str(cached_preview)).expanduser()
                if cached_preview
                else None
            )
            if preview_path is not None and not preview_path.exists():
                preview_path = None
            requested_preview = inputs.get("voice_preview_output_path")
            if preview_path is not None and requested_preview:
                import shutil

                requested = Path(str(requested_preview)).expanduser()
                requested.parent.mkdir(parents=True, exist_ok=True)
                if requested.resolve() != preview_path.resolve():
                    shutil.copy2(preview_path, requested)
                preview_path = requested
            return (
                str(record["voice"]),
                False,
                str(preview_path) if preview_path is not None else None,
            )

        response = http.post(
            self.VOICE_DESIGN_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=self._build_voice_design_payload(
                model=model,
                profile_id=profile_id,
            ),
            timeout=(10, 120),
        )
        data = ensure_success(response)
        voice = data.get("output", {}).get("voice") or data.get("output", {}).get(
            "voice_id"
        )
        if not voice:
            raise RuntimeError("DashScope Voice Design returned no output.voice")
        preview_path: Path | None = None
        preview_audio = data.get("output", {}).get("preview_audio", {})
        preview_data = (
            preview_audio.get("data")
            if isinstance(preview_audio, dict)
            else None
        )
        if preview_data:
            try:
                preview_bytes = base64.b64decode(
                    str(preview_data), validate=True
                )
                response_format = str(
                    preview_audio.get("response_format") or "wav"
                ).lower()
                suffix = response_format if response_format in {
                    "wav", "mp3", "opus", "pcm"
                } else "wav"
                configured_preview = inputs.get("voice_preview_output_path")
                if configured_preview:
                    preview_path = Path(str(configured_preview)).expanduser()
                else:
                    preview_path = (
                        self._voice_cache_path().parent
                        / "voice-previews"
                        / (
                            f"{account_scope}-{model}-{profile_id}-"
                            f"{self._profile_hash(VOICE_DESIGN_PROFILES[profile_id])}."
                            f"{suffix}"
                        )
                    )
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_preview = preview_path.with_name(
                    f".{preview_path.name}.{os.getpid()}.tmp"
                )
                temporary_preview.write_bytes(preview_bytes)
                temporary_preview.replace(preview_path)
            except (OSError, ValueError):
                # Preserve/cache the newly created voice even if preview
                # persistence fails, preventing a duplicate creation retry.
                preview_path = None
        cache[key] = {
            "voice": str(voice),
            "model": model,
            "profile_id": profile_id,
            "account_scope": account_scope,
            "profile_hash": self._profile_hash(VOICE_DESIGN_PROFILES[profile_id]),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "preview_path": str(preview_path) if preview_path else None,
        }
        try:
            self._write_voice_cache(cache)
        except OSError:
            # Voice creation already succeeded.  A cache permission problem
            # must not discard the usable voice ID and trigger an immediate
            # duplicate creation on retry.
            pass
        return (
            str(voice),
            True,
            str(preview_path) if preview_path is not None else None,
        )

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        model_was_explicit = "model" in inputs
        model = str(inputs.get("model", "qwen3-tts-flash"))
        input_data: dict[str, Any] = {
            "text": inputs["text"],
            "voice": inputs.get("voice", "Cherry"),
            "language_type": inputs.get("language_type", "Auto"),
        }
        # Preserve the legacy no-model call shape while preventing instructions
        # from leaking into explicit non-instruct models such as Voice Design.
        if inputs.get("instructions") and (
            "instruct" in model or not model_was_explicit
        ):
            input_data["instructions"] = inputs["instructions"]
            input_data["optimize_instructions"] = True
        return {"model": model, "input": input_data}

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
        from tools.analysis.audio_probe import probe_duration

        started = time.time()
        model = str(inputs.get("model", "qwen3-tts-flash"))
        try:
            voice, voice_created, voice_preview_path = self._resolve_voice(
                api_key=api_key,
                inputs=inputs,
                http=http,
            )
            resolved_inputs = {**inputs, "voice": voice}
            payload = self._build_payload(resolved_inputs)
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
            audio_url = data.get("output", {}).get("audio", {}).get("url")
            if not audio_url:
                return ToolResult(
                    success=False,
                    error="DashScope TTS returned no output.audio.url",
                )

            download = http.get(str(audio_url), timeout=(10, 120))
            if int(getattr(download, "status_code", 0) or 0) >= 400:
                ensure_success(download)
            content = bytes(getattr(download, "content", b""))
            if not content:
                return ToolResult(
                    success=False,
                    error="DashScope TTS download returned an empty audio file",
                )

            output_path = Path(str(inputs.get("output_path", "dashscope_tts.wav")))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)
            duration = probe_duration(output_path)
            return ToolResult(
                success=True,
                data={
                    "provider": "dashscope",
                    "model": payload["model"],
                    "voice": voice,
                    "voice_created": voice_created,
                    "voice_preview_path": voice_preview_path,
                    "voice_profile": inputs.get("voice_profile"),
                    "language_type": payload["input"].get("language_type", "Auto"),
                    "text_length": len(str(inputs["text"])),
                    "audio_duration_seconds": round(duration, 2) if duration else None,
                    "output": str(output_path),
                    # Keep the temporary URL in the in-memory result so the
                    # ASR provider can consume it without another upload.
                    "audio_url": str(audio_url),
                    "usage": data.get("usage", {}),
                    "request_id": data.get("request_id"),
                },
                artifacts=[
                    str(output_path),
                    *([voice_preview_path] if voice_preview_path else []),
                ],
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
                model=model,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"DashScope TTS failed: {self._safe_error(exc)}",
                duration_seconds=round(time.time() - started, 2),
                model=model,
            )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return safe_error_text(exc)
