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
- a song that ends without a usable timing (every source lost, ``alignment_failed``, an
  unusable timeline, ``uncertain``, a lasting ``provider_error``, a document of another source,
  an anchor ``disagree``) is missing: a spare replaces it, at most 3 rounds, then
  ``analysis_failed``; spotdown unreachable, an outage or the budget spent is
  ``analysis_unavailable`` (retryable); an error of any type is ``internal`` (not swallowed). There
  is no list from MOWL's sections (ADR 0040 amendment, #1354).
"""

import json
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.songs import (
    pick_analyse,
    pick_analyse_batch,
    pick_match_batch,
    pick_match_budget,
    pick_outcome,
    pick_refine,
)
from classbeat.spotdown import (
    outage_policy,
    song_analysis_policy,
    song_match_policy,
    track_facts_calc,
    track_fit_policy,
)
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.song_match_record_model import KEY as MATCH_KEY
from pickfake import call, entries, turn
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
def waits(songenv: SongEnv, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    made: list[float] = []

    async def sleep(_run: object, seconds: float) -> None:
        made.append(seconds)

    monkeypatch.setattr(pick_match_batch, "_sleep", sleep)
    return made


@pytest.fixture(autouse=True)
def analyses(spotdown: FakeSpotdown, waits: list[float]) -> None:
    docs(spotdown)


async def listed(songenv: SongEnv, rider: Rider) -> tuple[UUID, Any]:
    songenv.script = script()
    job_id, revision_id, _ = await songenv.waiting(rider)
    return job_id, songenv.revision(revision_id)


async def run_on(songenv: SongEnv) -> tuple[UUID, Any]:
    return await listed(songenv, await songenv.rider())


async def ended(songenv: SongEnv, rider: Rider | None = None) -> tuple[UUID, str, str]:
    """One attempt of a new pick: (job id, the job's state, the category of the latest
    ``retry_scheduled`` or ``failed`` event)."""
    rider = rider or await songenv.rider()
    songenv.script = script()
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    return job_id, row.state, ending(songenv, job_id)


def ending(songenv: SongEnv, job_id: UUID) -> str:
    event = songenv.sql(
        "SELECT payload ->> 'category' AS category FROM job_events WHERE job_id = :j "
        "AND kind IN ('retry_scheduled', 'failed') ORDER BY seq DESC LIMIT 1",
        j=job_id,
    ).first()
    return "" if event is None else event.category


def starts(fake: FakeSpotdown) -> list[dict[str, Any]]:
    return [json.loads(s.body) for s in fake.seen if s.path == "/v2/analyses"]


def songs_of(revision: Any) -> list[str]:
    return [s["spotify_id"] for s in revision.content["songs"]]


def labelled(revision: Any) -> list[int]:
    """The positions labelled ``timing approximate``: exactly the songs spotdown times."""
    songs = revision.content["songs"]
    assert {s.get("timing") for s in songs} <= {"approximate", None}
    return [p for p, s in enumerate(songs) if s.get("timing") == "approximate"]


def entry(revision: Any, position: int) -> dict[str, Any]:
    return next(e for e in revision.provenance[KEY]["songs"] if e["position"] == position)


def budget_row(songenv: SongEnv, job_id: UUID) -> Any:
    return songenv.sql(
        "SELECT spent_seconds, unavailable FROM spotdown_song_budgets WHERE job_id = :j", j=job_id
    ).first()


def swap_reasons(revision: Any) -> dict[int, list[str]]:
    return {s["position"]: s["reasons"] for s in revision.provenance[KEY]["swaps"]}


# --- the chosen songs only ------------------------------------------------------------------


async def test_only_the_chosen_songs_are_analysed_and_each_is_approximate(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    job_id, revision = await run_on(songenv)
    assert songs_of(revision) == [song_id(n) for n in SONGS]  # every song fits: nothing swapped
    assert labelled(revision) == list(range(8))
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
    assert budget_row(songenv, job_id).unavailable is False


async def test_a_cached_analysis_is_ready_at_once(songenv: SongEnv, spotdown: FakeSpotdown) -> None:
    for n in SONGS:
        spotdown.add_analysis(None, f"youtube:{vid(n)}")
    _, revision = await run_on(songenv)
    assert not [s for s in spotdown.seen if s.path.startswith("/v2/jobs/")]  # no poll
    assert all(e["use"] == "spotdown" for e in revision.provenance[KEY]["songs"])


# --- on: plan section 6, row by row -------------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["source_unavailable", "not_found", "processing_failed:decode_error"]
)
async def test_a_lost_source_moves_to_the_alternate(
    songenv: SongEnv, spotdown: FakeSpotdown, status: str
) -> None:
    ends(spotdown, vid(4), status)
    _, revision = await run_on(songenv)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked.count(f"youtube:{vid(4)}") == 1  # deterministic: never the same audio again
    assert f"youtube:{alt(4)}" in asked
    assert entry(revision, 2)["use"] == "spotdown"


async def test_every_source_lost_makes_that_song_missing_and_a_spare_replaces_it(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    ends(spotdown, vid(4), "source_unavailable")
    ends(spotdown, alt(4), "source_unavailable")
    _, revision = await run_on(songenv)
    assert songs_of(revision) == [song_id(5) if n == 4 else song_id(n) for n in SONGS]
    assert swap_reasons(revision) == {2: ["source_unavailable"]}
    assert labelled(revision) == list(range(8))  # every song of the list is spotdown's
    assert [e["use"] for e in revision.provenance[KEY]["songs"]] == ["spotdown"] * 8
    assert [p for p, _, _ in timing_marks(revision.provenance)] == list(range(8))


async def test_one_retry_after_a_timeout_on_the_same_source(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    ends(spotdown, vid(4), "processing_failed:timeout", "done")
    _, revision = await run_on(songenv)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked.count(f"youtube:{vid(4)}") == 2
    assert f"youtube:{alt(4)}" not in asked
    assert entry(revision, 2)["use"] == "spotdown"


async def test_alignment_failed_is_missing_without_an_alternate(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    ends(spotdown, vid(4), "alignment_failed")
    _, revision = await run_on(songenv)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert f"youtube:{alt(4)}" not in asked
    assert swap_reasons(revision) == {2: ["alignment_failed"]}


async def test_an_unusable_timeline_is_missing_with_its_reason(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    spotdown.documents[f"youtube:{vid(4)}"] = youtube_doc(vid(4), bpm(4), residual=2.5)
    _, revision = await run_on(songenv)
    assert swap_reasons(revision) == {2: ["residual_too_large"]}
    assert songs_of(revision)[2] == song_id(5)


async def test_a_document_of_another_source_is_not_used(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    spotdown.documents[f"youtube:{vid(4)}"] = youtube_doc(vid(5), bpm(4))  # not what was asked
    _, revision = await run_on(songenv)
    assert swap_reasons(revision) == {2: ["bad_document"]}


@pytest.mark.parametrize("status", ["uncertain", "provider_error"])
async def test_uncertain_and_a_lasting_provider_error_make_the_song_missing(
    songenv: SongEnv, spotdown: FakeSpotdown, status: str
) -> None:
    ends(spotdown, vid(4), status, status)
    _, revision = await run_on(songenv)
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked.count(f"youtube:{vid(4)}") == (2 if status == "provider_error" else 1)
    assert swap_reasons(revision) == {2: [status]}


async def test_a_provider_error_that_clears_on_its_retry_is_used(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    ends(spotdown, vid(4), "provider_error", "done")
    _, revision = await run_on(songenv)
    assert entry(revision, 2)["use"] == "spotdown"


async def test_a_provider_outage_ends_analysis_unavailable_and_swaps_nothing(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    for n in SONGS[:4]:  # 4 of 8: at least half and at least 3
        ends(spotdown, vid(n), "provider_error", "provider_error")
    docs(spotdown, wrong_bpm=(10,))  # would be replaced, but an outage swaps nothing
    job_id, state, category = await ended(songenv)
    assert (state, category) == ("queued", "analysis_unavailable")
    assert songenv.row(job_id).approval_revision_id is None
    asked = {a["source_ref"] for a in starts(spotdown)}
    assert asked == {f"youtube:{vid(n)}" for n in SONGS}  # no spare was asked for


async def test_three_provider_errors_of_eight_are_not_an_outage_each_is_replaced(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    for n in SONGS[:3]:
        ends(spotdown, vid(n), "provider_error", "provider_error")
    _, revision = await run_on(songenv)
    assert swap_reasons(revision) == {0: ["provider_error"], 1: ["provider_error"],
                                      2: ["provider_error"]}  # fmt: skip
    assert [e["use"] for e in revision.provenance[KEY]["songs"]] == ["spotdown"] * 8


async def test_rate_limited_waits_inside_the_budget_and_asks_again(
    songenv: SongEnv, spotdown: FakeSpotdown, waits: list[float]
) -> None:
    ends(spotdown, vid(4), "rate_limited", "done")
    job_id, revision = await run_on(songenv)
    assert entry(revision, 2)["use"] == "spotdown"
    assert 45.0 in waits  # the job's retry_after_s
    spent = songenv.sql(
        "SELECT spent_seconds FROM spotdown_song_budgets WHERE job_id = :j", j=job_id
    ).scalar_one()
    assert spent == pytest.approx(sum(waits))  # every wait charged


async def test_the_budget_spent_ends_analysis_unavailable(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    waits: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nearly_spent(_run: object) -> tuple[float, bool]:
        return 599.0, False  # one second left: no poll pause fits

    stage_budget = SimpleNamespace(
        open=nearly_spent,
        charge=pick_match_budget.charge,
        mark_unavailable=pick_match_budget.mark_unavailable,
    )
    monkeypatch.setattr(pick_analyse, "budget", stage_budget)  # the match stage's stays
    job_id, state, category = await ended(songenv)
    assert (state, category) == ("queued", "analysis_unavailable")
    assert waits == []
    assert songenv.row(job_id).approval_revision_id is None


# (how, the attempt's ending): spotdown cut off after the match got through
FAILURES = [
    ("connect_error", ("queued", "analysis_unavailable")),
    ("503", ("queued", "analysis_unavailable")),
    ("302", ("queued", "analysis_unavailable")),
    ("401", ("queued", "analysis_unavailable")),
    ("413", ("failed", "analysis_failed")),
    ("400", ("failed", "analysis_failed")),
    ("bad_answer", ("failed", "analysis_failed")),
    ("409", ("failed", "internal")),
]
REASONS = {"connect_error": "connection", "503": "server_error", "302": "blocked",
           "401": "unauthorized", "413": "too_large", "400": "rejected",
           "bad_answer": "bad_answer", "409": "conflict"}  # fmt: skip


@pytest.mark.parametrize(("how", "ending_of"), FAILURES, ids=[f[0] for f in FAILURES])
async def test_each_failure_kind_of_the_analysis_ends_its_category_and_makes_no_list(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    how: str,
    ending_of: tuple[str, str],
) -> None:
    if how == "bad_answer":
        spotdown.inject["/v2/analyses"] = [httpx.Response(200, json={"what": 1})]
    elif how == "connect_error":
        original = SpotdownClient.start_analysis

        async def cut(self: SpotdownClient, *a: Any, **k: Any) -> Any:
            spotdown.connect_error = True  # the match got through; the analysis does not
            return await original(self, *a, **k)

        monkeypatch.setattr(SpotdownClient, "start_analysis", cut)
    else:
        spotdown.inject["/v2/analyses"] = [httpx.Response(int(how))]
    with caplog.at_level(logging.INFO):
        job_id, state, category = await ended(songenv)
    assert (state, category) == ending_of
    assert songenv.row(job_id).approval_revision_id is None
    assert outage_policy.log_message(REASONS[how]) in caplog.text  # type: ignore[arg-type]


# --- the refinement -------------------------------------------------------------------------


async def test_an_unsuitable_song_is_replaced_alone_from_the_spares(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    docs(spotdown, wrong_bpm=(4,))  # the climb's BPM is not the one it was chosen with
    _, revision = await run_on(songenv)
    ids = songs_of(revision)
    assert ids == [song_id(5) if n == 4 else song_id(n) for n in SONGS]  # the spare climb
    assert [s["spotify_id"] for p, s in enumerate(revision.content["songs"]) if p != 2] == [
        song_id(n) for n in SONGS if n != 4
    ]  # the other songs stay in their slots
    assert labelled(revision) == [p for p, _, _ in timing_marks(revision.provenance)]
    record = revision.provenance[KEY]
    assert record["rounds"] == 1
    (swap,) = record["swaps"]
    assert (swap["position"], swap["reasons"], swap["round"]) == (2, ["bpm_unusable"], 1)
    assert swap["original"]["spotify_id"] == song_id(4)
    assert entry(revision, 2)["use"] == "spotdown"  # the spare, analysed in round 1
    asked = [a["source_ref"] for a in starts(spotdown)]
    assert asked == [*[f"youtube:{vid(n)}" for n in SONGS], f"youtube:{vid(5)}"]


async def test_a_warmup_without_a_low_start_is_unsuitable(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    spotdown.documents[f"youtube:{vid(0)}"] = youtube_doc(vid(0), bpm(0), low_start=False)
    _, revision = await run_on(songenv)
    assert songs_of(revision)[0] == song_id(1)
    assert revision.provenance[KEY]["swaps"][0]["reasons"] == ["structure_missing"]


async def test_at_most_two_rounds_then_accepted_with_a_warning(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    docs(spotdown, wrong_bpm=(6, 7, 8, 9))  # every sprint and every spare sprint
    _, revision = await run_on(songenv)
    record = revision.provenance[KEY]
    assert record["rounds"] == track_fit_policy.MAX_ROUNDS
    warned = [e["position"] for e in record["songs"] if e.get("warning")]
    assert warned == [4, 5]
    assert {s["round"] for s in record["swaps"]} <= {1, 2}
    assert len(starts(spotdown)) == 8 + 2  # the two spares once each; no third round


async def test_at_most_a_third_of_the_list_per_round(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    docs(spotdown, wrong_bpm=(0, 2, 4, 6))  # four unsuitable of eight: cap 2 a round
    _, revision = await run_on(songenv)
    record = revision.provenance[KEY]
    by_round: dict[int, list[int]] = {}
    for s in record["swaps"]:
        by_round.setdefault(s["round"], []).append(s["position"])
    assert all(len(p) <= 8 // 3 for p in by_round.values())
    assert sorted(p for ps in by_round.values() for p in ps) == [0, 1, 2, 4]


async def test_no_third_round_even_when_spares_are_left(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    # Round 1 replaces positions 0 and 1 (the cap), whose spares do not fit either; round 2 finds
    # no spare for them; positions 2 and 4 would have spares (5 and 8) in a third round.
    docs(spotdown, wrong_bpm=(0, 1, 2, 3, 4, 6))
    _, revision = await run_on(songenv)
    record = revision.provenance[KEY]
    assert record["rounds"] == track_fit_policy.MAX_ROUNDS
    assert [s["position"] for s in record["swaps"]] == [0, 1]
    assert [e["position"] for e in record["songs"] if e.get("warning")] == [0, 1, 2, 4]
    assert songs_of(revision)[2] == song_id(4)


async def test_no_spare_means_accepted_with_a_warning(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    docs(spotdown, wrong_bpm=(12,))  # the cooldown; its spare 13 would make it the same song?
    spotdown.documents[f"youtube:{vid(13)}"] = youtube_doc(vid(13), bpm(13) * 1.2)
    _, revision = await run_on(songenv)
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


# (module, attribute, async, a scenario so the call is reached: None, a song to make unsuitable so
# the refinement runs (4), "ratelimit" (a rate-limited read) or "shared" (the match stage's own)
CALLS = [
    (pick_match_budget, "open", True, None),
    (pick_analyse, "SpotdownClient", False, None),
    (pick_analyse, "analysed_pool", False, None),
    (pick_analyse, "analyse_tracks", True, None),
    (pick_analyse, "outcome_of", False, None),
    (pick_outcome, "decide", False, None),
    (pick_outcome, "provenance_of", False, None),
    (pick_outcome, "reference_sections", False, None),
    (pick_outcome, "slot_need", False, None),
    (pick_outcome, "anchor_check", False, None),
    (pick_analyse, "analysis_record", False, None),
    (pick_refine, "spare_for", False, 4),
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
    (pick_outcome, "analysed", False, None),
    (pick_outcome, "alignment", False, None),
    (pick_outcome, "decoded_duration_check", False, None),
    (pick_analyse, "total_sec", False, None),
    (pick_analyse, "ChosenList", False, None),
    (track_facts_calc, "track_facts", False, None),
    # shared with the match stage: raises only once the analysis stage has started
    (song_match_policy, "provider_outage", False, "shared"),
    (song_match_policy, "wait_for", False, "shared"),
    (pick_analyse, "Session", False, None),
    (pick_analyse, "_log_done", False, None),
]


@pytest.mark.parametrize(
    ("n", "target"), list(enumerate(CALLS)), ids=[f"{c[0].__name__}.{c[1]}" for c in CALLS]
)
async def test_an_error_of_any_type_in_the_stage_is_internal_never_a_list(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    n: int,
    target: tuple[Any, str, bool, Any],
) -> None:
    """Nothing in the stage swallows an error (#1354): each call raising ends the attempt
    ``internal`` and writes no list."""
    owner, name, is_async, scenario = target
    if scenario == 4:
        docs(spotdown, wrong_bpm=(4,))
    if scenario == "ratelimit":
        ends(spotdown, vid(4), "rate_limited", "done")
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
    job_id, state, category = await ended(songenv)
    assert len(called) >= (2 if is_open else 1), "the injected call was never made"
    assert (state, category) == ("failed", "internal")
    assert songenv.row(job_id).approval_revision_id is None


async def test_a_failing_unavailable_flag_write_is_not_swallowed(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_run: object) -> None:
        raise OSError("database refused")

    monkeypatch.setattr(pick_match_budget, "mark_unavailable", refuse)
    spotdown.inject["/v2/analyses"] = [httpx.Response(503)]
    _, state, category = await ended(songenv)
    assert (state, category) == ("failed", "internal")  # not hidden behind analysis_unavailable


# --- logs ---------------------------------------------------------------------------------------


async def test_nothing_secret_or_identifying_is_logged(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    caplog: pytest.LogCaptureFixture,
) -> None:
    docs(spotdown, wrong_bpm=(4,))
    ends(spotdown, vid(6), "provider_error", "provider_error")
    with caplog.at_level(logging.DEBUG):
        await run_on(songenv)
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "spotdown song analysis" in logged
    for secret in (SERVICE_KEY, "youtube:", "vid0000", "alt0000", "Ride Song", "Made-up Band"):
        assert secret not in logged
    assert all(song_id(n) not in logged for n in range(20))


# --- a replace: the kept songs are not analysed -------------------------------------------------


async def test_a_replace_analyses_only_its_new_song(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    job_id, revision, _ = await songenv.waiting(rider)  # the first pick's own requests
    spotdown.seen.clear()
    class_id = str(songenv.row(job_id).class_id)
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    kept = [("kept", 0, "warmup"), ("kept", 1, "build"), ("pool", 0, "climb")]
    rest = [(2, "recovery"), (3, "sprint"), (4, "sprint"), (5, "recovery"), (6, "cooldown")]
    songenv.script = {
        "model": "fake-model",
        "turns": [
            turn(call("search_tracks", {"query": "beta"}, expect={"ok": True})),
            turn(
                call(
                    "submit_pool",
                    entries(*[(r, "climb") for r in range(3, 9)]),  # songs 13 to 18
                    expect={"accepted": True},
                )
            ),
            *choose_turns(kept + [("kept", i, role) for i, role in rest]),
        ],
    }
    seed = {"song_list_revision_id": str(revision), "replace": [2]}
    _, new_id, _ = await songenv.waiting(rider, class_id=class_id, seed=seed)
    new = songenv.revision(new_id)
    assert new.content["songs"][2]["spotify_id"] == song_id(13)
    # The match batch is the 6 new candidates: only the one chosen (song 13) is analysed.
    assert [a["source_ref"] for a in starts(spotdown)] == [f"youtube:{vid(13)}"]
    # The record holds every song: the kept ones carry the seed list's entry, the new one its own.
    assert [e["position"] for e in new.provenance[KEY]["songs"]] == list(range(8))
    seeded = {e["position"]: e for e in songenv.revision(revision).provenance[KEY]["songs"]}
    for e in new.provenance[KEY]["songs"]:
        if e["position"] != 2:
            assert e == {**seeded[e["position"]], "kept": True}  # marked as carried


async def test_approve_starts_phase_two_with_a_fresh_budget(songenv: SongEnv) -> None:
    """The wait spent in phases 1 is not charged to phase 2 (#1354): Approve resets the row."""
    rider = await songenv.rider()
    songenv.script = script()
    job_id, revision_id, digest = await songenv.waiting(rider)
    songenv.sql(
        "UPDATE spotdown_song_budgets SET spent_seconds = 400, unavailable = true "
        "WHERE job_id = :j",
        j=job_id,
    )
    assert (await rider.approve(job_id, revision_id, digest)).status_code == 200
    row = budget_row(songenv, job_id)
    assert (row.spent_seconds, row.unavailable) == (0, False)


# The sprint at position 4 is song 6; its spares are 8, then 9, then none. The recovery at
# position 3 (song 10) has no spare at all.
# (songs whose sources are all lost, MAX_ROUNDS or None, the job's state, its category)
MISSING = [
    ([6], None, "waiting_for_approval", None),
    ([6, 8], None, "waiting_for_approval", None),  # two spare rounds fit in the three
    ([6, 8, 9], None, "failed", "analysis_failed"),  # no spare for the third: item 5
    ([10], None, "failed", "analysis_failed"),  # no spare at all
    ([6], 1, "waiting_for_approval", None),
    ([6, 8], 1, "failed", "analysis_failed"),  # a second round is over the limit (a spare left)
]


@pytest.mark.parametrize(
    ("lost", "limit", "state", "category"),
    MISSING,
    ids=lambda v: "-".join(map(str, v)) if isinstance(v, list) else str(v),
)
async def test_a_song_that_stays_missing_ends_analysis_failed_and_makes_no_list(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    lost: list[int],
    limit: int | None,
    state: str,
    category: str | None,
) -> None:
    for n in lost:
        ends(spotdown, vid(n), "source_unavailable")
        ends(spotdown, alt(n), "source_unavailable")
    if limit is not None:
        monkeypatch.setattr(song_match_policy, "MAX_ROUNDS", limit)
    job_id, got_state, got_category = await ended(songenv)
    assert got_state == state
    if category is None:
        assert songenv.row(job_id).approval_revision_id is not None  # a list for approval
    else:
        assert got_category == category
        assert songenv.row(job_id).approval_revision_id is None  # nothing stored or shown
