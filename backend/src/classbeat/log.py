"""Logging: stdlib ``logging``, one JSON object per line on stdout."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import IO, Any

_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


# ADR 0003 section 10: never logged. The session, device, CSRF, OAuth and pending cookies; the
# Authorization and X-CSRF-Token headers (and whole Cookie headers); query strings on /oauth/*.
# Our own access log never records any of them; this catches the rest (an exception message
# or a library that dumps a request).
_REDACTED = "[redacted]"
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(__Host-classbeat_\w+)\s*=\s*[^;\s\"',]+"), rf"\1={_REDACTED}"),
    (
        re.compile(
            r"(?i)\b(authorization|x-csrf-token|cookie|set-cookie)\b"
            r"([\"']?\s*[:=]\s*[\"']?)[^\r\n\"']*"
        ),
        rf"\1\2{_REDACTED}",
    ),
    (re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+"), f"Bearer {_REDACTED}"),
    (re.compile(r"(/oauth/[^\s?\"'#]*)\?[^\s\"'#]*"), r"\1"),
)


def redact(value: str) -> str:
    """Remove cookie values, credential headers and OAuth query strings from ``value``."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact_any(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_any(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [_redact_any(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class JsonFormatter(logging.Formatter):
    """Formats a record as a single-line JSON object.

    ``extra={...}`` fields are included; they must never hold secrets.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                payload.setdefault(key, value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(
            _redact_any(payload), default=lambda v: redact(str(v)), ensure_ascii=False
        )


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    """Send all logging (ours, uvicorn's, SQLAlchemy's) to one JSON handler on the root logger."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    # SQL statements carry bound parameters (token hashes, emails); even at DEBUG they are
    # never logged (ADR 0003 section 10).
    for noisy in ("sqlalchemy.engine", "sqlalchemy.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
