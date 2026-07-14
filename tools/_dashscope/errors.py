"""Structured, credential-safe DashScope HTTP error handling.

DashScope returns useful error codes in JSON response bodies.  Calling
``response.raise_for_status()`` directly discards those codes, which makes it
impossible for a pipeline to distinguish transient throttling from the
terminal ``AllocationQuota.FreeTierOnly`` condition used by zero-cost runs.
"""

from __future__ import annotations

import os
import re
from typing import Any


FREE_TIER_ERROR_CODES = {
    "AllocationQuota.FreeTierOnly",
}

RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}

_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def _normalized_code(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.casefold())


def _terminal_reason(code: str) -> str | None:
    normalized = _normalized_code(code)
    if code in FREE_TIER_ERROR_CODES:
        return "free_tier_exhausted"
    if "allocationquota" in normalized:
        return "allocation_quota"
    if any(
        marker in normalized
        for marker in ("arrearage", "billing", "insufficientbalance")
    ):
        return "billing"
    if any(
        marker in normalized
        for marker in ("modelunavailable", "modelnotfound", "invalidmodel")
    ):
        return "model_unavailable"
    if any(
        marker in normalized
        for marker in ("invalidapikey", "accessdenied", "unauthorized")
    ):
        return "authorization"
    return None


def safe_error_text(value: object) -> str:
    """Return error text without API keys or temporary/signed URLs.

    Requests transport exceptions often include the complete request URL.
    DashScope media URLs carry temporary query-string credentials, so the
    safest cross-provider rule is to redact the entire URL from user-visible
    error text rather than trying to recognize every signature parameter.
    """

    text = str(value)
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return _URL_PATTERN.sub("[redacted-url]", text)


def _redact(value: object) -> str:
    """Return a printable value with the configured API key removed."""
    return safe_error_text(value)


class DashscopeAPIError(RuntimeError):
    """An HTTP/API failure with fields suitable for pipeline governance."""

    def __init__(
        self,
        *,
        http_status: int,
        code: str | None,
        message: str,
        request_id: str | None = None,
    ) -> None:
        self.http_status = int(http_status)
        self.code = code or "UnknownError"
        self.message = _redact(message)
        self.request_id = request_id
        self.quota_exhausted = self.code in FREE_TIER_ERROR_CODES
        self.terminal_reason = _terminal_reason(self.code)
        self.terminal = self.terminal_reason is not None
        self.retryable = (
            not self.terminal
            and (
                self.http_status in RETRYABLE_HTTP_STATUSES
                or any(
                    marker in _normalized_code(self.code)
                    for marker in (
                        "throttling",
                        "internalerror",
                        "serviceunavailable",
                    )
                )
            )
        )
        super().__init__(self._render())

    def _render(self) -> str:
        request_suffix = f", request_id={self.request_id}" if self.request_id else ""
        return (
            f"DashScope API error: HTTP {self.http_status}, "
            f"code {self.code}: {self.message}{request_suffix}"
        )


def _json_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        status = int(getattr(response, "status_code", 0) or 0)
        raise DashscopeAPIError(
            http_status=status,
            code="NonJSONResponse",
            message="DashScope returned a non-JSON response.",
        ) from exc
    if not isinstance(payload, dict):
        status = int(getattr(response, "status_code", 0) or 0)
        raise DashscopeAPIError(
            http_status=status,
            code="InvalidResponse",
            message="DashScope returned a JSON value that is not an object.",
        )
    return payload


def ensure_success(response: Any) -> dict[str, Any]:
    """Parse a DashScope response and raise a structured error when needed.

    Some DashScope endpoints return a top-level ``code`` even when the HTTP
    transport succeeds, so both the status code and the payload are checked.
    """
    payload = _json_payload(response)
    http_status = int(getattr(response, "status_code", 0) or 0)
    error_value = payload.get("error")
    error_payload = error_value if isinstance(error_value, dict) else {}
    code = payload.get("code") or error_payload.get("code")
    if http_status < 400 and not code and not error_value:
        return payload

    message = payload.get("message") or error_payload.get("message")
    if not message and isinstance(error_value, str):
        message = error_value
    if not message:
        message = "Unknown DashScope error"
    request_id = payload.get("request_id")
    raise DashscopeAPIError(
        http_status=http_status,
        code=str(code) if code else None,
        message=str(message),
        request_id=str(request_id) if request_id else None,
    )


def tool_error_data(error: DashscopeAPIError) -> dict[str, Any]:
    """Serialize safe error metadata into ``ToolResult.data``."""
    return {
        "error_code": error.code,
        "http_status": error.http_status,
        "request_id": error.request_id,
        "retryable": error.retryable,
        "quota_exhausted": error.quota_exhausted,
        "terminal": error.terminal,
        "terminal_reason": error.terminal_reason,
    }
