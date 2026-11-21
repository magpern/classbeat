"""SD-P6 stage B (#1303, ADR 0040 section 8, plan section 6): the analysis stage's reactions per
status, their bounds and what a slot needs (``classbeat.spotdown.song_analysis_policy``)."""

import pytest

from classbeat.spotdown import song_analysis_policy as policy
from classbeat.spotdown.failure_policy import RETRY_AFTER_DEFAULT, TrackFailure
from classbeat.spotdown.song_analysis_policy import Tried, react, slot_need
from classbeat.spotdown.song_match_policy import RETRY_PAUSE_SECONDS


def first() -> Tried:
    return Tried(requests=1)


def test_rate_limited_waits_and_is_not_an_attempt() -> None:
    r = react(TrackFailure("rate_limited", retry_after_s=45.0), first(), 3)
    assert (r.step, r.pause, r.tried) == ("wait", 45.0, first())
    r = react(TrackFailure("rate_limited"), first(), 3)
    assert (r.step, r.pause) == ("wait", RETRY_AFTER_DEFAULT)


def test_provider_error_is_retried_once_then_mowl_sections() -> None:
    r = react(TrackFailure("provider_error"), first(), 3)
    assert (r.step, r.pause, r.tried.retried_error) == ("again", RETRY_PAUSE_SECONDS, True)
    r2 = react(TrackFailure("provider_error"), r.tried, 3)
    assert (r2.step, r2.reason) == ("sections", "provider_error")


def test_one_retry_after_a_timeout_then_the_alternate() -> None:
    timeout = TrackFailure("processing_failed", failure_kind="timeout")
    r = react(timeout, first(), 3)
    assert (r.step, r.tried.retried_timeout, r.tried.source) == ("again", True, 0)
    r2 = react(timeout, r.tried, 3)
    assert (r2.step, r2.tried.source, r2.tried.retried_timeout) == ("alternate", 1, False)
    # The alternate gets its own one timeout retry.
    assert react(timeout, r2.tried, 3).step == "again"


@pytest.mark.parametrize("kind", ["decode_error", "out_of_memory", "analysis_error", None])
def test_processing_failed_is_not_retried_on_the_same_audio(kind: str | None) -> None:
    r = react(TrackFailure("processing_failed", failure_kind=kind), first(), 3)
    assert (r.step, r.tried.source) == ("alternate", 1)


@pytest.mark.parametrize("status", ["source_unavailable", "not_found", "processing_failed"])
def test_at_most_two_alternates_then_mowl_sections(status: str) -> None:
    failure = TrackFailure(status)  # pyright: ignore[reportArgumentType]
    tried = first()
    steps = []
    for _ in range(4):
        r = react(failure, tried, 3)
        steps.append(r.step)
        tried = r.tried
        if r.step == "sections":
            assert r.reason == status
            break
    assert steps == ["alternate", "alternate", "sections"]
    # Never past the third source, whatever the track says it has.
    assert react(failure, Tried(source=2, requests=1), 9).step == "sections"
    # A single source: straight to MOWL's sections.
    assert react(failure, first(), 1).step == "sections"


@pytest.mark.parametrize(
    ("status", "reason"),
    [("alignment_failed", "alignment_failed"), ("uncertain", "uncertain")],
)
def test_alignment_failed_and_uncertain_keep_mowl_sections(status: str, reason: str) -> None:
    r = react(TrackFailure(status), first(), 3)  # pyright: ignore[reportArgumentType]
    assert (r.step, r.reason) == ("sections", reason)


@pytest.mark.parametrize(
    "failure",
    [
        TrackFailure("rate_limited", retry_after_s=5.0),
        TrackFailure("provider_error"),
        TrackFailure("source_unavailable"),
        TrackFailure("processing_failed", failure_kind="timeout"),
    ],
)
def test_at_most_max_requests_per_track(failure: TrackFailure) -> None:
    r = react(failure, Tried(requests=policy.MAX_REQUESTS), 3)
    assert (r.step, r.reason) == ("sections", "too_many_requests")
    assert react(failure, Tried(requests=policy.MAX_REQUESTS - 1), 3).step != "sections"


def test_a_slot_needs_its_chosen_bpm_and_length_and_a_warmup_a_low_start() -> None:
    need = slot_need("warmup", 128.0, 240)
    assert (need.bpm_min, need.bpm_max, need.min_s, need.max_s) == (128.0, 128.0, 230.0, 250.0)
    assert need.structure == frozenset({"low_energy_start"})
    assert need.needs_exact is False
    assert slot_need("climb", 100.0, 5).structure == frozenset()
    assert slot_need("climb", 100.0, 5).min_s == 0.0
