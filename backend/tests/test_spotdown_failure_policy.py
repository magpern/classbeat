"""The failure taxonomy of ADR 0040 section 8 (``classbeat.spotdown.failure_policy``), table-driven:
match results, job views and HTTP statuses to typed results; ``retry_after_s`` clamped."""

import pytest

from classbeat.spotdown.answer_model import JobView
from classbeat.spotdown.failure_policy import (
    RETRY_AFTER_DEFAULT,
    RETRY_AFTER_MAX,
    RETRY_AFTER_MIN,
    AnalysisReady,
    Matched,
    Pending,
    TrackFailure,
    UncertainMatch,
    Unreachable,
    clamp_retry_after,
    http_outcome,
    job_outcome,
    match_outcome,
)
from classbeat.spotdown.match_model import Candidate, MatchResult

BEST = Candidate("youtube:abc", 0.9, "confident", 200.0, 1.0, "ok", None, None)


def job(status: str, outcome: str | None = None, **extra: object) -> JobView:
    return JobView(
        "job_1",
        status,
        None,
        outcome,
        extra.get("retry_after_s"),  # pyright: ignore[reportArgumentType]
        extra.get("analysis_id"),  # pyright: ignore[reportArgumentType]
        extra.get("failure_kind"),  # pyright: ignore[reportArgumentType]
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (MatchResult("t", "matched", BEST, (), "1", None), Matched(BEST, (), "1")),
        (MatchResult("t", "uncertain", BEST, (), "1", None), UncertainMatch(BEST, (), "1")),
        (MatchResult("t", "not_found", None, (), None, None), TrackFailure("not_found")),
        (
            MatchResult("t", "source_unavailable", None, (), None, None),
            TrackFailure("source_unavailable"),
        ),
        (MatchResult("t", "provider_error", None, (), None, None), TrackFailure("provider_error")),
        (
            MatchResult("t", "rate_limited", None, (), None, 30.0),
            TrackFailure("rate_limited", retry_after_s=30.0),
        ),
        (
            MatchResult("t", "rate_limited", None, (), None, 10_000.0),
            TrackFailure("rate_limited", retry_after_s=RETRY_AFTER_MAX),
        ),
    ],
)
def test_match_results(result: MatchResult, expected: object) -> None:
    assert match_outcome(result) == expected


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        (job("queued"), Pending("job_1", "queued")),
        (job("running"), Pending("job_1", "running")),
        (job("done", analysis_id="an_1"), AnalysisReady("an_1")),
        (
            job("done", "alignment_failed", analysis_id="an_1", failure_kind="alignment_failed"),
            TrackFailure("alignment_failed", failure_kind="alignment_failed", analysis_id="an_1"),
        ),
        (
            job("failed", "processing_failed", failure_kind="timeout"),
            TrackFailure("processing_failed", failure_kind="timeout"),
        ),
        (job("failed", "not_found"), TrackFailure("not_found")),
        (job("failed", "uncertain"), TrackFailure("uncertain")),
        (job("failed", "source_unavailable"), TrackFailure("source_unavailable")),
        (job("failed", "provider_error"), TrackFailure("provider_error")),
        (
            job("failed", "rate_limited", retry_after_s=0.0),
            TrackFailure("rate_limited", retry_after_s=RETRY_AFTER_MIN),
        ),
        (
            job("failed", "alignment_failed"),
            TrackFailure("processing_failed", failure_kind="analysis_error"),
        ),
    ],
)
def test_job_views(view: JobView, expected: object) -> None:
    assert job_outcome(view) == expected


@pytest.mark.parametrize(
    ("status", "code", "retry", "expected"),
    [
        (429, None, 5.0, TrackFailure("rate_limited", retry_after_s=5.0)),
        (429, None, None, TrackFailure("rate_limited", retry_after_s=RETRY_AFTER_DEFAULT)),
        (401, None, None, Unreachable("unauthorized")),
        (403, None, None, Unreachable("unauthorized")),
        (409, None, None, Unreachable("conflict")),
        (413, None, None, Unreachable("too_large")),
        (422, "source_disabled", None, Unreachable("source_disabled")),
        (422, "scope_required", None, Unreachable("rejected")),
        (400, None, None, Unreachable("rejected")),
        (404, None, None, Unreachable("rejected")),
        (302, None, None, Unreachable("blocked")),
        (500, None, None, Unreachable("server_error")),
        (503, None, None, Unreachable("server_error")),
        (200, None, None, Unreachable("rejected")),  # a success the call did not expect
    ],
)
def test_http_statuses(
    status: int, code: str | None, retry: float | None, expected: object
) -> None:
    assert http_outcome(status, code, retry) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, RETRY_AFTER_DEFAULT),
        (float("nan"), RETRY_AFTER_DEFAULT),
        (float("inf"), RETRY_AFTER_DEFAULT),
        (-5.0, RETRY_AFTER_MIN),
        (42.0, 42.0),
        (1e9, RETRY_AFTER_MAX),
    ],
)
def test_retry_after_is_clamped(value: float | None, expected: float) -> None:
    assert clamp_retry_after(value) == expected
