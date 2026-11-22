"""SD-P6 stage A (#1303, ADR 0040 sections 2 and 8): the pure rules of the Spotify song pick's
spotdown match (the only analysis path, #1354): the owner list parser (still the mix setting's), the
per-track use and reactions, the outage and budget bounds, spares and replacement rounds, and the
provenance record. No I/O."""

from uuid import UUID

import pytest

from classbeat.config import Settings
from classbeat.spotdown import song_match_policy as policy
from classbeat.spotdown.failure_policy import Matched, TrackFailure, UncertainMatch
from classbeat.spotdown.match_model import Candidate as Source
from classbeat.spotdown.song_match_record_model import KEY, match_record
from classbeat.spotdown.song_swap_calc import (
    Candidate,
    Swap,
    pair_swaps,
    ranked_spares,
    short,
    to_replace,
)

A = UUID("00000000-0000-4000-8000-00000000000a")
B = UUID("00000000-0000-4000-8000-00000000000b")


def source(ref: str, confidence: float, band: str = "confident") -> Source:
    return Source(f"youtube:{ref}", confidence, band, 200.0, 0.5, "ok", None, None)


# --- the settings -------------------------------------------------------------------------------


def test_the_spotify_switch_owner_list_and_source_settings_are_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spotdown is the only analysis path: no switch, no allowlist, no source list (#1354, #1355).
    A set variable is ignored (extra settings are not an error), never read."""
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SPOTIFY", "allowlist")
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS", "*")  # would be refused if it were read
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SOURCES", "nonsense")  # likewise
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SHADOW", "true")
    settings = Settings()
    for name in (
        "spotdown_spotify",
        "spotdown_spotify_owners",
        "spotdown_sources",
        "spotdown_shadow",
    ):
        assert not hasattr(settings, name)


# --- the per-track use and reactions ------------------------------------------------------------


def test_a_matched_track_is_for_timing_with_its_alternates_as_sources() -> None:
    alternates = (source("alt1", 0.7, "uncertain"), source("alt2", 0.65, "uncertain"))
    made = policy.track_match(Matched(source("best", 0.92), alternates, "youtube-1"))
    assert (made.use, made.status, made.band, made.confidence) == (
        "timing",
        "matched",
        "confident",
        0.92,
    )
    assert [s.source for s in made.sources] == ["youtube:best", "youtube:alt1", "youtube:alt2"]
    assert made.matcher_version == "youtube-1"
    assert "youtube:" not in repr(made)


@pytest.mark.parametrize(
    ("outcome", "use"),
    [
        (UncertainMatch(source("u", 0.7, "uncertain"), (), "youtube-1"), "missing"),
        (TrackFailure("provider_error"), "missing"),  # after its one retry
        (TrackFailure("not_found"), "missing"),
        (TrackFailure("source_unavailable"), "missing"),
        (TrackFailure("processing_failed"), "missing"),  # never sent by /v2/match: fail safe
    ],
)
def test_every_other_status_is_missing_never_kept_on_sections(outcome: object, use: str) -> None:
    made = policy.track_match(outcome)  # pyright: ignore[reportArgumentType]
    assert made.use == use  # the song is missing: a spare, a round, then analysis_failed
    assert made.sources == ()
    assert {"timing", "missing"} == policy.USES  # there is no ``sections`` use


def test_reactions_retry_a_provider_error_once_and_wait_for_a_rate_limit() -> None:
    error, limited = TrackFailure("provider_error"), TrackFailure("rate_limited", 10.0)
    assert policy.reaction(error, retried=False) == "retry"
    assert policy.reaction(error, retried=True) == "final"
    assert policy.reaction(limited, retried=False) == "wait"
    assert policy.reaction(limited, retried=True) == "wait"  # not an attempt
    for final in (TrackFailure("not_found"), TrackFailure("source_unavailable")):
        assert policy.reaction(final, retried=False) == "final"
    assert policy.reaction(Matched(source("b", 0.9), (), "1"), retried=False) == "final"


@pytest.mark.parametrize(
    ("errors", "batch", "outage"),
    [(2, 2, False), (2, 3, False), (3, 3, True), (3, 6, True), (3, 7, False), (7, 14, True),
     (6, 14, False), (25, 50, True)],
)  # fmt: skip
def test_a_provider_outage_is_three_tracks_and_half_a_batch(
    errors: int, batch: int, outage: bool
) -> None:
    assert policy.provider_outage(errors, batch) is outage


def test_the_budget_is_ten_minutes_and_a_wait_that_does_not_fit_is_not_made() -> None:
    assert policy.WAIT_SECONDS == 600.0
    assert policy.remaining(0.0) == 600.0
    assert policy.remaining(550.0) == 50.0
    assert policy.remaining(900.0) == 0.0
    assert policy.wait_for(30.0, 50.0) == 30.0
    assert policy.wait_for(50.0, 50.0) is None
    assert policy.wait_for(5.0, 0.0) is None
    assert policy.wait_for(-3.0, 10.0) == 0.0
    assert (policy.MAX_ROUNDS, policy.MAX_ALTERNATES) == (3, 2)


# --- spares and replacement rounds --------------------------------------------------------------


def cand(n: int, role: str, use: str = "timing", confidence: float | None = 0.9) -> Candidate:
    status = {"timing": "matched", "missing": "not_found"}[use]
    return Candidate(f"id{n}", role, f"Song {n}", f"Band {n}", use, status, confidence)  # pyright: ignore[reportArgumentType]


NEED = {"climb": 2, "sprint": 4, "cooldown": 2}


def test_spares_fill_the_list_without_a_round() -> None:
    found = [cand(0, "climb"), cand(1, "sprint"), cand(2, "sprint"), cand(3, "cooldown")]
    found += [cand(n, "warmup") for n in range(4, 8)]
    assert not short(found, 8, NEED)
    assert short(found[:-1], 8, NEED)  # one song short
    no_climb = [cand(9, "warmup"), *found[1:]]
    assert short(no_climb, 8, NEED)  # a role the plan needs is lacking
    missing_only = [cand(10, "climb", "missing"), *found[1:]]
    assert short(missing_only, 8, NEED)  # a missing candidate is never usable


def test_a_round_replaces_only_the_shortfall_and_pairs_by_role() -> None:
    found = [cand(1, "sprint"), cand(2, "sprint"), cand(3, "cooldown")]
    found += [cand(n, "warmup") for n in range(4, 9)]  # 8 usable, no climb
    missing = [
        cand(20, "climb", "missing"),
        cand(21, "climb", "missing"),
        cand(22, "sprint", "missing"),
    ]
    wanted = to_replace(missing, found, 8, NEED)
    assert [c.spotify_id for c in wanted] == ["id20"]  # one climb slot lacking, nothing more
    fresh = [cand(30, "sprint"), cand(31, "climb", "missing"), cand(32, "climb")]
    swaps, left = pair_swaps(wanted, fresh, 2)
    assert swaps == {"id32": Swap("id20", "Song 20", "Band 20", "not_found", 2)}
    assert left == []
    swaps, left = pair_swaps(wanted, [cand(33, "climb", "missing")], 1)
    assert (swaps, [c.spotify_id for c in left]) == ({}, ["id20"])


def test_ranked_spares_timing_first_then_confidence_then_pool_order() -> None:
    found = [
        cand(0, "climb", "missing", 0.8),
        cand(1, "climb", "timing", 0.86),
        cand(2, "climb", "timing", 0.95),
        cand(3, "climb", "missing"),
        cand(4, "sprint", "timing", 0.99),
        cand(5, "climb", "timing", 0.86),
        cand(6, "climb", "timing", 0.97),
    ]
    spares = ranked_spares(found, {"id6"}, "climb")
    assert [c.spotify_id for c in spares] == ["id2", "id1", "id5"]


# --- the record ---------------------------------------------------------------------------------


def test_the_record_holds_codes_and_numbers_and_the_swap_never_a_source() -> None:
    matches = {
        "idA": policy.track_match(Matched(source("vidAAAAAAAA", 0.9234), (), "youtube-1")),
        "idB": policy.track_match(TrackFailure("provider_error")),
    }
    swaps = {"idA": Swap("idX", "Old song", "Old band", "not_found", 1)}
    made = match_record(["", "idA", "idB"], matches, swaps, rounds=1, candidates=9, missing=2)
    record = made[KEY]
    assert record["songs"] == [
        {
            "position": 1,
            "status": "matched",
            "use": "timing",
            "band": "confident",
            "confidence": 0.923,
        },
        {"position": 2, "status": "provider_error", "use": "missing"},
    ]
    assert record["swaps"] == [
        {
            "position": 1,
            "original": {"spotify_id": "idX", "title": "Old song", "artist": "Old band"},
            "reason": "not_found",
            "round": 1,
        }
    ]
    assert record["matcher_versions"] == ["youtube-1"]
    assert (record["v"], record["policy_version"], record["rounds"]) == (2, "song-match-2", 1)
    assert "youtube:" not in repr(record)
    assert "vidAAAAAAAA" not in repr(record)
