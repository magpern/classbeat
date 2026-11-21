"""SD-P6 stage B (#1303, ADR 0040 sections 2, 3, 6 and 8, plan sections 1 and 6): spotdown's
analysis of a Spotify pick's chosen songs and the bounded refinement, as a job on the fakes: the
fake Spotify, the fake MOWL pool, the fake model and the fake spotdown, in process
(``spotdownfake``). Never the network: the autouse netguard refuses any other connection.

- only the chosen new songs are analysed (never the pool), by their match's YouTube source, with no
  ``cache_scope`` and an opaque Spotify reference; every analysis is at best ``approximate``;
- each status of plan section 6 has its reaction (the alternates, the one retry, the one timeout
  retry, ``alignment_failed``, the outage, ``rate_limited`` inside the budget);
- an unsuitable song is replaced alone from the spares, at most 2 rounds and a third of the list
  per round, then accepted with a warning;
- spotdown unreachable, an outage or an error of any type: the list as chosen, no record, and
  every later run of the job on today's path.
"""

import json
import logging
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.songs import pick_analyse, pick_analyse_batch, pick_match_batch, pick_match_budget
from classbeat.spotdown import (
    song_analysis_policy,
    song_match_config,
    song_match_policy,
    track_facts_calc,
    track_fit_policy,
)
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.song_match_record_model import KEY as MATCH_KEY
from songanalysisdocs import youtube_doc
from songtools import Rider, SongEnv, choose_turns, pool_turns, song_id
from spotdownfake import SERVICE_KEY, FakeSpotdown, spotdown
from spotifycore import mowl_bpm

__all__ = ["spotdown"]  # the fixture

pytestmark = pytest.mark.usefixtures("songenv")

LIST = [(0, "warmup"), (2, "build"), (4, "climb"), (10, "recovery"), (6, "sprint"),
        (7, "sprint"), (11, "recovery"), (12, "cooldown")]  # fmt: skip
SONGS = [n for n, _ in LIST]
ERRORS = (OSError, ValueError, KeyError, TypeError, ZeroDivisionError, AttributeError,
          LookupError, PermissionError)  # fmt: skip


def vid(n: int) -> str:
    return f"vid{n:08d}"


def alt(n: int) -> str:
    return f"alt{n:08d}"


def bpm(n: int) -> float:
    return float(mowl_bpm(song_id(n)))


def script() -> dict[str, Any]:
    return {"model": "fake-model", "turns": [*pool_turns(), *choose_turns()]}


def docs(fake: FakeSpotdown, *, wrong_bpm: tuple[int, ...] = (), **kw: Any) -> None:
    """Every pool song's best and alternate sources analyse as that song (BPM as MOWL's)."""
    for n in range(20):
        tempo = bpm(n) * 1.2 if n in wrong_bpm else bpm(n)
        fake.documents[f"youtube:{vid(n)}"] = youtube_doc(vid(n), tempo, **kw)
        fake.documents[f"youtube:{alt(n)}"] = youtube_doc(alt(n), tempo, **kw)


def ends(fake: FakeSpotdown, source: str, *how: str) -> None:
    fake.ends_by_source[f"youtube:{source}"] = list(how)


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    made: list[float] = []

    async def sleep(_run: object, seconds: float) -> None:
        made.append(seconds)

    monkeypatch.setattr(pick_match_batch, "_sleep", sleep)
    return made


@pytest.fixture
def on(spotdown: FakeSpotdown, waits: list[float]) -> Iterator[Callable[[Rider], None]]:
    def switch(rider: Rider) -> None:
        song_match_config.configure(
            song_match_config.MatchConfig(
                "allowlist", frozenset({rider.user_id}), frozenset({"youtube"})
            )
        )

    docs(spotdown)
    try:
        yield switch
    finally:
        song_match_config.configure(None)


async def listed(songenv: SongEnv, rider: Rider) -> tuple[UUID, Any]:
    songenv.script = script()
    job_id, revision_id, _ = await songenv.waiting(rider)
    return job_id, songenv.revision(revision_id)


async def run_on(songenv: SongEnv, on: Callable[[Rider], None]) -> tuple[UUID, Any]:
    rider = await songenv.rider()
    on(rider)
    return await listed(songenv, rider)


def starts(fake: FakeSpotdown) -> list[dict[str, Any]]:
    return [json.loads(s.body) for s in fake.seen if s.path == "/v2/analyses"]


def songs_of(revision: Any) -> list[str]:
    return [s["spotify_id"] for s in revision.content["songs"]]


def entry(revision: Any, position: int) -> dict[str, Any]:
    return next(e for e in revision.provenance[KEY]["songs"] if e["position"] == position)


def fallback(songenv: SongEnv, job_id: UUID) -> bool:
    row = songenv.sql(
        "SELECT fallback FROM spotdown_song_budgets WHERE job_id = :j", j=job_id
    ).first()
    return bool(row.fallback)


async def baseline(songenv: SongEnv) -> Any:
    other = await songenv.rider()
    return (await listed(songenv, other))[1]


# --- off ----------------------------------------------------------------------------------------


async def test_off_analyses_nothing(songenv: SongEnv, spotdown: FakeSpotdown) -> None:
    rider = await songenv.rider()
    _, revision = await listed(songenv, rider)
    assert spotdown.seen == []
    assert KEY not in revision.provenance


# --- on: the chosen songs only ------------------------------------------------------------------


async def test_only_the_chosen_songs_are_analysed_and_each_is_approximate(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    today = await baseline(songenv)
    job_id, revision = await run_on(songenv, on)
    assert revision.content == today.content  # every song fits: nothing swapped
    asked = starts(spotdown)
    assert [a["source_ref"] for a in asked] == [f"youtube:{vid(n)}" for n in SONGS]  # not the pool
    for body, n in zip(asked, SONGS, strict=True):
        assert "cache_scope" not in body  # a youtube analysis is global
        assert body["reference"]["kind"] == "spotify"
        assert body["reference"]["duration_s"] == 240.0
        assert song_id(n) not in json.dumps(body)  # an opaque reference id
    record = revision.provenance[KEY]
    assert [e["position"] for e in record["songs"]] == list(range(8))
    for e in record["songs"]:
        assert (e["use"], e["source_kind"], e["timing"]["class"]) == (
            "spotdown", "youtube", "approximate",
        )  # fmt: skip
        assert e["timing"]["reason"] == "matched_recording"
        assert e["fit"] == {"fits": True, "reasons": []}
    assert (record["rounds"], record["swaps"]) == (0, [])
    assert set(record["policy_versions"]) == {
        "spotdown_map_policy", "timing_class_policy", "track_fit_policy",
    }  # fmt: skip
    stored = json.dumps(revision.provenance)
    for leak in ("youtube:", "vid0000", "cache_scope", "client_ref", "http", "job_"):
        assert leak not in stored
    assert timing_marks(revision.provenance) == [(n, "approximate", "youtube") for n in range(8)]
    assert MATCH_KEY in revision.provenance
    assert fallback(songenv, job_id) is False


async def test_a_cached_analysis_is_ready_at_once(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    for n in SONGS:
        spotdown.add_analysis(None, f"youtube:{vid(n)}")
    _, revision = await run_on(songenv, on)
    assert not [s for s in spotdown.seen if s.path.startswith("/v2/jobs/")]  # no poll
    assert all(e["use"] == "spotdown" for e in revision.provenance[KEY]["songs"])


# --- on: plan section 6, row by row -------------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["source_unavailable", "not_found", "processing_failed:decode_error"]
)
async def test_a_lost_source_moves_to_the_alternate(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None], status: str
) -> None:
    ends(spotdown, vid(4), status)
    _, revision = await run_on(songenv, on)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked.count(f"youtube:{vid(4)}") == 1  # deterministic: never the same audio again
    assert f"youtube:{alt(4)}" in asked
    assert entry(revision, 2)["use"] == "spotdown"


async def test_every_source_lost_keeps_mowls_sections_for_that_song_only(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    today = await baseline(songenv)
    ends(spotdown, vid(4), "source_unavailable")
    ends(spotdown, alt(4), "source_unavailable")
    _, revision = await run_on(songenv, on)
    assert revision.content == today.content
    assert entry(revision, 2) == {"position": 2, "use": "sections", "reason": "source_unavailable"}
    assert all(entry(revision, p)["use"] == "spotdown" for p in range(8) if p != 2)
    assert [p for p, _, _ in timing_marks(revision.provenance)] == [0, 1, 3, 4, 5, 6, 7]


async def test_one_retry_after_a_timeout_on_the_same_source(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    ends(spotdown, vid(4), "processing_failed:timeout", "done")
    _, revision = await run_on(songenv, on)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked.count(f"youtube:{vid(4)}") == 2
    assert f"youtube:{alt(4)}" not in asked
    assert entry(revision, 2)["use"] == "spotdown"


async def test_alignment_failed_keeps_mowls_sections_without_an_alternate(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    ends(spotdown, vid(4), "alignment_failed")
    _, revision = await run_on(songenv, on)
    assert f"youtube:{alt(4)}" not in [a["source_ref"] for a in starts(spotdown)]
    assert entry(revision, 2) == {"position": 2, "use": "sections", "reason": "alignment_failed"}


async def test_an_unusable_timeline_is_discarded_with_its_evidence(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    spotdown.documents[f"youtube:{vid(4)}"] = youtube_doc(vid(4), bpm(4), residual=2.5)
    _, revision = await run_on(songenv, on)
    e = entry(revision, 2)
    assert (e["use"], e["reason"], e["timing"]["class"]) == (
        "sections", "residual_too_large", "unusable",
    )  # fmt: skip
    assert e["timing"]["evidence"]["residual_s"] == 2.5
    assert 2 not in [p for p, _, _ in timing_marks(revision.provenance)]


async def test_a_document_of_another_source_is_not_used(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    spotdown.documents[f"youtube:{vid(4)}"] = youtube_doc(vid(5), bpm(4))  # not what was asked
    _, revision = await run_on(songenv, on)
    assert entry(revision, 2) == {"position": 2, "use": "sections", "reason": "bad_document"}


@pytest.mark.parametrize("status", ["uncertain", "provider_error"])
async def test_uncertain_and_a_lasting_provider_error_keep_mowls_sections(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None], status: str
) -> None:
    ends(spotdown, vid(4), status, status)
    _, revision = await run_on(songenv, on)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked.count(f"youtube:{vid(4)}") == (2 if status == "provider_error" else 1)
    assert entry(revision, 2) == {"position": 2, "use": "sections", "reason": status}


async def test_a_provider_error_that_clears_on_its_retry_is_used(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    ends(spotdown, vid(4), "provider_error", "done")
    _, revision = await run_on(songenv, on)
    assert entry(revision, 2)["use"] == "spotdown"


async def test_a_provider_outage_keeps_the_list_and_swaps_nothing(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    today = await baseline(songenv)
    for n in SONGS[:4]:  # 4 of 8: at least half and at least 3
        ends(spotdown, vid(n), "provider_error", "provider_error")
    docs(spotdown, wrong_bpm=(10,))  # would be replaced, but an outage swaps nothing
    job_id, revision = await run_on(songenv, on)
    assert revision.content == today.content
    assert KEY not in revision.provenance
    assert fallback(songenv, job_id) is True


async def test_three_provider_errors_of_eight_are_not_an_outage(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    for n in SONGS[:3]:
        ends(spotdown, vid(n), "provider_error", "provider_error")
    _, revision = await run_on(songenv, on)
    assert [entry(revision, p)["use"] for p in range(8)] == ["sections"] * 3 + ["spotdown"] * 5


async def test_rate_limited_waits_inside_the_budget_and_asks_again(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None], waits: list[float]
) -> None:
    ends(spotdown, vid(4), "rate_limited", "done")
    job_id, revision = await run_on(songenv, on)
    assert entry(revision, 2)["use"] == "spotdown"
    assert 45.0 in waits  # the job's retry_after_s
    spent = songenv.sql(
        "SELECT spent_seconds FROM spotdown_song_budgets WHERE job_id = :j", j=job_id
    ).scalar_one()
    assert spent == pytest.approx(sum(waits))  # every wait charged


async def test_the_budget_spent_keeps_mowls_sections(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    on: Callable[[Rider], None],
    waits: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nearly_spent(_run: object) -> tuple[float, bool]:
        return 599.0, False  # one second left: no poll pause fits

    stage_budget = SimpleNamespace(
        open=nearly_spent,
        charge=pick_match_budget.charge,
        mark_fallback=pick_match_budget.mark_fallback,
    )
    monkeypatch.setattr(pick_analyse, "budget", stage_budget)  # the match stage's stays
    _, revision = await run_on(songenv, on)
    assert all(e["reason"] == "timed_out" for e in revision.provenance[KEY]["songs"])
    assert waits == []


@pytest.mark.parametrize("how", ["connect_error", "503", "bad_answer"])
async def test_spotdown_unreachable_is_the_list_as_chosen_byte_for_byte(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    on: Callable[[Rider], None],
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    today = await baseline(songenv)
    docs(spotdown, wrong_bpm=(4,))  # would be replaced: nothing is, the list stays as chosen
    if how == "503":
        spotdown.inject["/v2/analyses"] = [httpx.Response(503)]
    elif how == "bad_answer":
        spotdown.inject["/v2/analyses"] = [httpx.Response(200, json={"what": 1})]
    else:
        original = SpotdownClient.start_analysis

        async def cut(self: SpotdownClient, *a: Any, **k: Any) -> Any:
            spotdown.connect_error = True  # the match got through; the analysis does not
            return await original(self, *a, **k)

        monkeypatch.setattr(SpotdownClient, "start_analysis", cut)
    job_id, revision = await run_on(songenv, on)
    assert revision.content == today.content
    assert KEY not in revision.provenance
    assert MATCH_KEY in revision.provenance  # the match stage's own record stands
    assert fallback(songenv, job_id) is True


# --- on: the refinement -------------------------------------------------------------------------


async def test_an_unsuitable_song_is_replaced_alone_from_the_spares(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    today = await baseline(songenv)
    docs(spotdown, wrong_bpm=(4,))  # the climb's BPM is not the one it was chosen with
    _, revision = await run_on(songenv, on)
    ids = songs_of(revision)
    assert ids == [song_id(5) if n == 4 else song_id(n) for n in SONGS]  # the spare climb
    others = [s for p, s in enumerate(revision.content["songs"]) if p != 2]
    assert others == [s for p, s in enumerate(today.content["songs"]) if p != 2]
    record = revision.provenance[KEY]
    assert record["rounds"] == 1
    (swap,) = record["swaps"]
    assert (swap["position"], swap["reasons"], swap["round"]) == (2, ["bpm_unusable"], 1)
    assert swap["original"]["spotify_id"] == song_id(4)
    assert entry(revision, 2)["use"] == "spotdown"  # the spare, analysed in round 1
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked == [*[f"youtube:{vid(n)}" for n in SONGS], f"youtube:{vid(5)}"]


async def test_a_warmup_without_a_low_start_is_unsuitable(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    spotdown.documents[f"youtube:{vid(0)}"] = youtube_doc(vid(0), bpm(0), low_start=False)
    _, revision = await run_on(songenv, on)
    assert songs_of(revision)[0] == song_id(1)
    assert revision.provenance[KEY]["swaps"][0]["reasons"] == ["structure_missing"]


async def test_at_most_two_rounds_then_accepted_with_a_warning(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    docs(spotdown, wrong_bpm=(6, 7, 8, 9))  # every sprint and every spare sprint
    _, revision = await run_on(songenv, on)
    record = revision.provenance[KEY]
    assert record["rounds"] == track_fit_policy.MAX_ROUNDS
    warned = [e["position"] for e in record["songs"] if e.get("warning")]
    assert warned == [4, 5]
    assert {s["round"] for s in record["swaps"]} <= {1, 2}
    assert len(starts(spotdown)) == 8 + 2  # the two spares once each; no third round


async def test_at_most_a_third_of_the_list_per_round(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    docs(spotdown, wrong_bpm=(0, 2, 4, 6))  # four unsuitable of eight: cap 2 a round
    _, revision = await run_on(songenv, on)
    record = revision.provenance[KEY]
    by_round: dict[int, list[int]] = {}
    for s in record["swaps"]:
        by_round.setdefault(s["round"], []).append(s["position"])
    assert all(len(p) <= 8 // 3 for p in by_round.values())
    assert sorted(p for ps in by_round.values() for p in ps) == [0, 1, 2, 4]


async def test_no_third_round_even_when_spares_are_left(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    # Round 1 replaces positions 0 and 1 (the cap), whose spares do not fit either; round 2 finds
    # no spare for them; positions 2 and 4 would have spares (5 and 8) in a third round.
    docs(spotdown, wrong_bpm=(0, 1, 2, 3, 4, 6))
    _, revision = await run_on(songenv, on)
    record = revision.provenance[KEY]
    assert record["rounds"] == track_fit_policy.MAX_ROUNDS
    assert [s["position"] for s in record["swaps"]] == [0, 1]
    assert [e["position"] for e in record["songs"] if e.get("warning")] == [0, 1, 2, 4]
    assert songs_of(revision)[2] == song_id(4)


async def test_no_spare_means_accepted_with_a_warning(
    songenv: SongEnv, spotdown: FakeSpotdown, on: Callable[[Rider], None]
) -> None:
    docs(spotdown, wrong_bpm=(12,))  # the cooldown; its spare 13 would make it the same song?
    spotdown.documents[f"youtube:{vid(13)}"] = youtube_doc(vid(13), bpm(13) * 1.2)
    _, revision = await run_on(songenv, on)
    record = revision.provenance[KEY]
    assert entry(revision, 7)["warning"] is True
    assert len(record["swaps"]) == 1  # 13 was tried; nothing is left for a second round


# --- on: an error of any type -------------------------------------------------------------------


def _raiser(error: type[BaseException], is_async: bool) -> Callable[..., Any]:
    def boom(*_args: object, **_kwargs: object) -> Any:
        raise error("injected")

    async def aboom(*_args: object, **_kwargs: object) -> Any:
        raise error("injected")

    return aboom if is_async else boom


# (module, attribute, async, a song to make unsuitable so the refinement runs)
CALLS = [
    (pick_match_budget, "open", True, None),
    (pick_analyse, "SpotdownClient", False, None),
    (pick_analyse, "analysed_pool", False, None),
    (pick_analyse, "analyse_tracks", True, None),
    (pick_analyse, "outcome_of", False, None),
    (pick_analyse, "decide", False, None),
    (pick_analyse, "provenance_of", False, None),
    (pick_analyse, "reference_sections", False, None),
    (pick_analyse, "slot_need", False, None),
    (pick_analyse, "analysis_record", False, None),
    (pick_analyse, "spare_for", False, 4),
    (pick_analyse_batch, "idempotency_key", False, None),
    (pick_analyse_batch, "reference_of", False, None),
    (pick_analyse_batch, "poll_delay", False, None),
    (pick_analyse_batch, "youtube_ref", False, None),
    (pick_analyse_batch, "wait", True, None),
    (pick_match_budget, "charge", True, None),
    (pick_match_batch, "_sleep", True, None),
    (song_analysis_policy, "react", False, "ratelimit"),
    (SpotdownClient, "start_analysis", True, None),
    (SpotdownClient, "poll", True, None),
    (SpotdownClient, "get_analysis", True, None),
    (track_fit_policy, "check", False, None),
    (track_fit_policy, "plan_round", False, None),
    (pick_analyse, "analysed", False, None),
    (pick_analyse, "alignment", False, None),
    (pick_analyse, "decoded_duration_check", False, None),
    (pick_analyse, "total_sec", False, None),
    (pick_analyse, "ChosenList", False, None),
    (track_facts_calc, "track_facts", False, None),
    # shared with the match stage: raises only once the analysis stage has started
    (song_match_policy, "provider_outage", False, "shared"),
    (song_match_policy, "wait_for", False, "shared"),
    (pick_analyse, "Session", False, None),
]


@pytest.mark.parametrize(
    ("n", "target"), list(enumerate(CALLS)), ids=[f"{c[0].__name__}.{c[1]}" for c in CALLS]
)
async def test_an_error_of_any_type_in_the_stage_gives_the_list_as_chosen(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    on: Callable[[Rider], None],
    monkeypatch: pytest.MonkeyPatch,
    n: int,
    target: tuple[Any, str, bool, Any],
) -> None:
    owner, name, is_async, scenario = target
    today = await baseline(songenv)
    if scenario == 4:
        docs(spotdown, wrong_bpm=(4,))
    if scenario == "ratelimit":
        ends(spotdown, vid(4), "rate_limited", "done")
    rider = await songenv.rider()
    on(rider)
    called: list[int] = []
    real = getattr(owner, name)
    error = ERRORS[n % len(ERRORS)]

    def counted(*a: Any, **k: Any) -> Any:
        if scenario == "shared" and not starts(spotdown):
            return real(*a, **k)
        called.append(1)
        return _raiser(error, is_async)(*a, **k)

    async def second_open(run: Any) -> Any:  # the match stage opens the budget first
        called.append(1)
        if len(called) == 1:
            return await real(run)
        raise error("injected")

    is_open = owner is pick_match_budget and name == "open"
    monkeypatch.setattr(owner, name, second_open if is_open else counted)
    _, revision = await listed(songenv, rider)
    assert len(called) >= (2 if is_open else 1), "the injected call was never made"
    assert revision.content == today.content
    assert KEY not in revision.provenance


async def test_a_failing_fallback_record_still_falls_back(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    on: Callable[[Rider], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_run: object) -> None:
        raise OSError("database refused")

    monkeypatch.setattr(pick_match_budget, "mark_fallback", refuse)
    spotdown.inject["/v2/analyses"] = [httpx.Response(503)]
    job_id, revision = await run_on(songenv, on)
    assert KEY not in revision.provenance
    assert fallback(songenv, job_id) is False


# --- logs ---------------------------------------------------------------------------------------


async def test_nothing_secret_or_identifying_is_logged(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    on: Callable[[Rider], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    docs(spotdown, wrong_bpm=(4,))
    ends(spotdown, vid(6), "provider_error", "provider_error")
    with caplog.at_level(logging.DEBUG):
        await run_on(songenv, on)
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "spotdown song analysis" in logged
    for secret in (SERVICE_KEY, "youtube:", "vid0000", "alt0000", "Ride Song", "Made-up Band"):
        assert secret not in logged
    assert all(song_id(n) not in logged for n in range(20))
