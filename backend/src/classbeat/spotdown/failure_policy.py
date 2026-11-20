"""The failure taxonomy of ADR 0040 section 8 as typed results. Pure: no I/O.

Every spotdown call ends in exactly one value of these types, never in an exception the caller
must parse:

- a **per-track status** (``TrackFailure``): ``not_found``, ``uncertain``, ``source_unavailable``,
  ``provider_error``, ``rate_limited`` (with ``retry_after_s``), ``processing_failed`` (with its
  ``failure_kind``) or ``alignment_failed`` (with the file-clock ``analysis_id``, which exists);
- **spotdown unreachable** (``Unreachable``) with a reason: nothing usable came back, so the whole
  class uses today's paths (spotdown is never a hard dependency);
- or the call's success value (``Matched``, ``UncertainMatch``, ``Pending``, ``AnalysisReady``).

The reactions (spare, alternate, replacement, fallback; the bounds per class) are the caller's
policy, not this module's. ``retry_after_s`` is clamped here to ``RETRY_AFTER_MIN`` to
``RETRY_AFTER_MAX`` (the ~10 min spotdown wait budget of section 8), whatever the answer says.
"""

import math
from dataclasses import dataclass
from typing import Final, Literal

from classbeat.spotdown.answer_model import JobView
from classbeat.spotdown.match_model import Candidate, MatchResult

RETRY_AFTER_MIN: Final = 1.0
RETRY_AFTER_MAX: Final = 600.0
RETRY_AFTER_DEFAULT: Final = 60.0  # a 429 without a usable ``Retry-After``

TrackStatus = Literal[
    "not_found",
    "uncertain",
    "source_unavailable",
    "provider_error",
    "rate_limited",
    "processing_failed",
    "alignment_failed",
]
UnreachableReason = Literal[
    "not_configured",  # no origin or no key in this process: nothing was sent
    "source_off",  # the source is not in ``CLASSBEAT_SPOTDOWN_SOURCES``: nothing was sent
    "source_disabled",  # spotdown's ``422 source_disabled``
    "blocked",  # the egress builder refused a request or a redirect
    "connection",  # connect, read or write error, a timeout or the total deadline
    "unauthorized",  # 401 or 403: the service key is wrong (an operator error)
    "conflict",  # 409: the Idempotency-Key was used for another body
    "too_large",  # 413, or an answer over its cap
    "rejected",  # any other 4xx
    "server_error",  # 5xx
    "bad_answer",  # an answer the contract does not allow
]


@dataclass(frozen=True, slots=True)
class TrackFailure:
    status: TrackStatus
    retry_after_s: float | None = None  # ``rate_limited`` only
    failure_kind: str | None = None  # ``processing_failed`` (and ``alignment_failed``)
    analysis_id: str | None = None  # ``alignment_failed`` only: the file-clock analysis


@dataclass(frozen=True, slots=True)
class Unreachable:
    reason: UnreachableReason


@dataclass(frozen=True, slots=True)
class Matched:
    best: Candidate
    alternates: tuple[Candidate, ...]
    matcher_version: str


@dataclass(frozen=True, slots=True)
class UncertainMatch:
    """``uncertain``: not used for timing; the best candidate and alternates are kept for the
    caller's own decision (keep with MOWL sections, or replace)."""

    best: Candidate
    alternates: tuple[Candidate, ...]
    matcher_version: str


@dataclass(frozen=True, slots=True)
class Pending:
    job_id: str
    status: Literal["queued", "running"]


@dataclass(frozen=True, slots=True)
class AnalysisReady:
    analysis_id: str


MatchOutcome = Matched | UncertainMatch | TrackFailure
JobOutcome = Pending | AnalysisReady | TrackFailure


def clamp_retry_after(value: float | None) -> float:
    """A wait in seconds within the bounds (``RETRY_AFTER_DEFAULT`` when missing or not finite)."""
    if value is None or isinstance(value, bool) or not math.isfinite(value):
        return RETRY_AFTER_DEFAULT
    return min(max(float(value), RETRY_AFTER_MIN), RETRY_AFTER_MAX)


def match_outcome(result: MatchResult) -> MatchOutcome:
    """One ``match-v1`` result as a typed outcome (``answer_model`` has checked its shape)."""
    if result.status in ("matched", "uncertain") and result.best is not None:
        kind = Matched if result.status == "matched" else UncertainMatch
        return kind(result.best, result.alternates, result.matcher_version or "")
    if result.status == "rate_limited":
        return TrackFailure("rate_limited", retry_after_s=clamp_retry_after(result.retry_after_s))
    if result.status in ("not_found", "source_unavailable", "provider_error"):
        return TrackFailure(result.status)  # pyright: ignore[reportArgumentType]
    return TrackFailure("provider_error")  # unreachable after answer_model; fail safe


def job_outcome(job: JobView) -> JobOutcome:
    """A ``job-v1`` view as a typed outcome: still pending, ready, or a per-track status."""
    if job.status in ("queued", "running"):
        return Pending(job.job_id, "queued" if job.status == "queued" else "running")
    if job.status == "done" and job.analysis_id is not None:
        if job.outcome == "alignment_failed":
            return TrackFailure(
                "alignment_failed", failure_kind="alignment_failed", analysis_id=job.analysis_id
            )
        return AnalysisReady(job.analysis_id)
    outcome = job.outcome
    if outcome == "rate_limited":
        return TrackFailure("rate_limited", retry_after_s=clamp_retry_after(job.retry_after_s))
    if outcome == "processing_failed":
        return TrackFailure("processing_failed", failure_kind=job.failure_kind)
    if outcome in ("not_found", "uncertain", "source_unavailable", "provider_error"):
        return TrackFailure(outcome)  # pyright: ignore[reportArgumentType]
    # A failed job with ``alignment_failed`` has no analysis (the contract says ``done``):
    # nothing usable, the same as a processing failure.
    return TrackFailure("processing_failed", failure_kind="analysis_error")


def http_outcome(
    status: int, error_code: str | None, retry_after: float | None
) -> TrackFailure | Unreachable:
    """A status that is not the call's success: ``429`` is ``rate_limited`` (not an attempt),
    everything else makes spotdown unreachable for this call, with a reason."""
    if status == 429:
        return TrackFailure("rate_limited", retry_after_s=clamp_retry_after(retry_after))
    if status in (401, 403):
        return Unreachable("unauthorized")
    if status == 409:
        return Unreachable("conflict")
    if status == 413:
        return Unreachable("too_large")
    if status == 422 and error_code == "source_disabled":
        return Unreachable("source_disabled")
    if 300 <= status < 400:
        return Unreachable("blocked")
    if 500 <= status <= 599:
        return Unreachable("server_error")
    return Unreachable("rejected")
