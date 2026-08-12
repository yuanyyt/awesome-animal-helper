"""Normalize billing failures from OpenAI-compatible model providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

API_BALANCE_ERROR_CODE = "API_BALANCE_EXHAUSTED"
API_BALANCE_ERROR_MESSAGE = "API 余额不足"

_BALANCE_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "quota exhausted",
    "quota has been exhausted",
    "quota_exceeded",
    "quota exceeded",
    "insufficient balance",
    "balance is insufficient",
    "account balance",
    "credit balance",
    "out of credit",
    "arrearage",
    "余额不足",
    "额度不足",
    "额度耗尽",
    "账户欠费",
)


def is_api_balance_exhausted(error: object) -> bool:
    """Return true only for explicit provider billing or quota exhaustion signals."""

    normalized = " ".join(_error_text(error)).casefold()
    return any(marker in normalized for marker in _BALANCE_MARKERS)


def api_balance_detail() -> dict[str, str]:
    """Return the stable public error contract shared by HTTP and WebSocket APIs."""

    return {
        "code": API_BALANCE_ERROR_CODE,
        "message": API_BALANCE_ERROR_MESSAGE,
    }


def _error_text(root: object) -> list[str]:
    values: list[str] = []
    pending: list[tuple[object, int]] = [(root, 0)]
    seen: set[int] = set()

    while pending:
        value, depth = pending.pop()
        if value is None or depth > 6 or id(value) in seen:
            continue
        seen.add(id(value))

        if isinstance(value, str):
            values.append(value)
            continue
        if isinstance(value, bytes):
            values.append(value.decode("utf-8", errors="ignore"))
            continue
        if isinstance(value, (int, float, bool)):
            continue
        if isinstance(value, Mapping):
            for key, item in value.items():
                values.append(str(key))
                pending.append((item, depth + 1))
            continue
        if isinstance(value, (list, tuple, set)):
            pending.extend((item, depth + 1) for item in value)
            continue

        if isinstance(value, BaseException):
            values.append(str(value))
            pending.append((value.__cause__, depth + 1))
            pending.append((value.__context__, depth + 1))

        for attribute in (
            "code",
            "type",
            "message",
            "reason",
            "text",
            "body",
            "error",
            "response",
        ):
            try:
                item: Any = getattr(value, attribute, None)
            except Exception:
                continue
            if item is not None and item is not value:
                pending.append((item, depth + 1))

    return values
