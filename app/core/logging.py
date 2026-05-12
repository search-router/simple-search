"""JSON access logger with secret masking."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any

_SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|x-api-key|token)", re.IGNORECASE)


def configure_logging(level: str = "INFO") -> None:
    """Install a single stdout JSON handler. Idempotent."""
    root = logging.getLogger()
    if getattr(root, "_search_service_configured", False):
        return
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root._search_service_configured = True  # type: ignore[attr-defined]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_FIELDS or key.startswith("_"):
                continue
            if _SECRET_KEY_RE.search(key):
                payload[key] = "***"
            else:
                payload[key] = mask_secrets(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_STD_RECORD_FIELDS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime",
    }
)


def mask_secrets(value: Any) -> Any:
    """Recursively replace values whose key matches a secret pattern."""
    if isinstance(value, Mapping):
        return {
            k: ("***" if _SECRET_KEY_RE.search(str(k)) else mask_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_secrets(v) for v in value)
    return value


_sample_lock = threading.Lock()
_last_sample_at: dict[tuple[int, str], float] = {}


def sampled_warning(
    logger: logging.Logger,
    event: str,
    *,
    min_interval_seconds: float = 1.0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit ``logger.warning(event, extra=...)`` at most once per interval.

    A Redis flap (or any other recoverable upstream) can otherwise fire one
    warning per request — every one of which runs the JSON formatter's
    recursive ``mask_secrets`` over ``extra``. Sampling keeps the signal
    (you still know it's happening) without amplifying it.
    """
    if not logger.isEnabledFor(logging.WARNING):
        return
    key = (id(logger), event)
    now = time.monotonic()
    with _sample_lock:
        last = _last_sample_at.get(key, 0.0)
        if now - last < min_interval_seconds:
            return
        _last_sample_at[key] = now
    logger.warning(event, extra=extra or {})
