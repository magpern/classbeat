"""spotdown's answers, parsed strictly into typed values (contract v2, ``schema/*-v1.json``).
Pure: no I/O.

Every parser takes the raw body and returns a frozen value or ``None`` for anything the schema
does not allow: not JSON (``NaN`` and ``Infinity`` included), another ``schema_version``, an
unknown field, a wrong type, or a cross-field rule broken (``best`` only for ``matched`` and
``uncertain``, ``retry_after_s`` only for ``rate_limited``, a ``band`` that disagrees with its
confidence, an ``analysis_id`` only on a ``done`` job ...). An id is kept only when it matches
the contract's id pattern, so it can go into a path (``request_model.check_id``). The caller turns
``None`` into ``Unreachable("bad_answer")`` (``failure_policy``).

The ``match-v1`` results are parsed in ``match_model`` with the helpers here. The analysis
itself (``analysis-v1``) is returned as the parsed JSON object after its version, its id and its
top-level groups are checked; mapping it is ``spotdown_map_policy``'s (PR 2 of #1301).
"""

import json
import math
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown.request_model import ID_PATTERN

JOB_OUTCOMES: Final = frozenset(
    {
        "not_found",
        "uncertain",
        "source_unavailable",
        "provider_error",
        "rate_limited",
        "processing_failed",
        "alignment_failed",
    }
)
FAILURE_KINDS: Final = frozenset(
    {"decode_error", "timeout", "out_of_memory", "analysis_error", "alignment_failed"}
)
_ANALYSIS_REQUIRED: Final = frozenset(
    {
        "schema_version",
        "analysis_id",
        "provenance",
        "signal",
        "tempo",
        "grid",
        "structure",
        "events",
    }
)
_TEXT_MAX: Final = 256


@dataclass(frozen=True, slots=True)
class JobView:
    job_id: str
    status: str  # queued, running, done or failed
    client_ref: str | None
    outcome: str | None
    retry_after_s: float | None
    analysis_id: str | None
    failure_kind: str | None


@dataclass(frozen=True, slots=True)
class Started:
    """``POST /v2/analyses``: ``200`` done at once (a cache hit) or ``202`` queued."""

    job_id: str
    status: str
    analysis_id: str | None


@dataclass(frozen=True, slots=True)
class Uploaded:
    upload_id: str
    sha256: str
    size: int


def _refuse_constant(_: str) -> object:
    raise ValueError("not a JSON number")


def json_object(raw: bytes | None) -> dict[str, object] | None:
    if raw is None:
        return None
    try:
        value: object = json.loads(raw, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]


def number(value: object, low: float = -math.inf, high: float = math.inf) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= _TEXT_MAX else None


def checked_id(value: object) -> str | None:
    return value if isinstance(value, str) and ID_PATTERN.fullmatch(value) else None


def parse_job(raw: bytes | None) -> JobView | None:
    """A ``job-v1`` object, with its ``allOf`` rules."""
    body = json_object(raw)
    keys = {"schema_version", "job_id", "status", "outcome"}
    extra = {"client_ref", "retry_after_s", "analysis_id", "failure_kind"}
    if body is None or not keys <= set(body) <= keys | extra:
        return None
    job_id, status, outcome = checked_id(body["job_id"]), body["status"], body["outcome"]
    ref, kind = body.get("client_ref"), body.get("failure_kind")
    analysis_id = checked_id(body["analysis_id"]) if "analysis_id" in body else None
    if body["schema_version"] != "job-v1" or job_id is None:
        return None
    if status not in ("queued", "running", "done", "failed"):
        return None
    if outcome is not None and outcome not in JOB_OUTCOMES:
        return None
    if ref is not None and (not isinstance(ref, str) or len(ref) > 128):
        return None
    if kind is not None and kind not in FAILURE_KINDS:
        return None
    if "analysis_id" in body and analysis_id is None:
        return None
    if status == "done" and (analysis_id is None or outcome not in (None, "alignment_failed")):
        return None
    if status == "failed" and (outcome is None or analysis_id is not None):
        return None
    if status in ("queued", "running") and (outcome is not None or analysis_id is not None):
        return None
    if (outcome == "rate_limited") != ("retry_after_s" in body):
        return None
    retry = number(body["retry_after_s"], 0.0) if "retry_after_s" in body else None
    if "retry_after_s" in body and retry is None:
        return None
    if outcome == "processing_failed" and kind not in FAILURE_KINDS - {"alignment_failed"}:
        return None
    return JobView(
        job_id,
        str(status),
        ref if isinstance(ref, str) else None,
        None if outcome is None else str(outcome),
        retry,
        analysis_id,
        None if kind is None else str(kind),
    )


def parse_started(raw: bytes | None, *, cached: bool) -> Started | None:
    """``POST /v2/analyses``: ``{"job_id", "status": "done", "analysis_id"}`` (``200``, a cache
    hit) or ``{"job_id", "status": "queued"}`` (``202``)."""
    body = json_object(raw)
    if body is None:
        return None
    job_id = checked_id(body.get("job_id"))
    if cached:
        analysis_id = checked_id(body.get("analysis_id"))
        ok = set(body) == {"job_id", "status", "analysis_id"} and body["status"] == "done"
        return Started(job_id, "done", analysis_id) if ok and job_id and analysis_id else None
    ok = set(body) == {"job_id", "status"} and body["status"] == "queued"
    return Started(job_id, "queued", None) if ok and job_id else None


def parse_uploaded(raw: bytes | None) -> Uploaded | None:
    """``PUT /v2/uploads``' ``201`` answer: ``{"upload_id", "sha256", "bytes"}``."""
    body = json_object(raw)
    if body is None or set(body) != {"upload_id", "sha256", "bytes"}:
        return None
    upload_id, digest, size = checked_id(body["upload_id"]), body["sha256"], body["bytes"]
    if upload_id is None or not isinstance(digest, str) or len(digest) != 64:
        return None
    if any(c not in "0123456789abcdef" for c in digest):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    return Uploaded(upload_id, digest, size)


def parse_lookup(raw: bytes | None) -> str | None:
    """A ``lookup-v1`` hit's ``analysis_id`` (a miss is a ``404``, never this shape)."""
    body = json_object(raw)
    if body is None or set(body) != {"schema_version", "analysis_id"}:
        return None
    return checked_id(body["analysis_id"]) if body["schema_version"] == "lookup-v1" else None


def parse_analysis(raw: bytes | None) -> dict[str, object] | None:
    """An ``analysis-v1`` document: the right version and every required group an object."""
    body = json_object(raw)
    if body is None or body.get("schema_version") != "analysis-v1":
        return None
    if not set(body) >= _ANALYSIS_REQUIRED:
        return None
    if checked_id(body["analysis_id"]) is None:
        return None
    groups = _ANALYSIS_REQUIRED - {"schema_version", "analysis_id", "events"}
    if not isinstance(body["events"], list):
        return None
    return body if all(isinstance(body[g], dict) for g in groups) else None


def parse_error_code(raw: bytes | None) -> str | None:
    """The ``code`` of the common error envelope ``{"error": {"code", "message"}}``."""
    body = json_object(raw)
    error = None if body is None else body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return code if isinstance(code, str) and code.isidentifier() and len(code) <= 64 else None
