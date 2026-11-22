"""SD-P6 stage A (#1303, ADR 0040 sections 2 and 8, plan section 6): the spotdown match of a
Spotify song pick as a job on the fakes: the fake Spotify, the fake MOWL pool, the fake model and
the fake spotdown (``spotdownfake``, in process; a track's match status is a ``[<status>]`` token
in its title). Never the network: the autouse netguard refuses any other connection.

- spotdown is the only path (ADR 0040 amendment "spotdown is the only analysis path", #1354): one
  ``/v2/match`` of the pool (metadata only, nothing downloaded); a candidate that is not
  ``matched`` is left out while the spares fill the list, else replaced by the song agent (at
  most 3 rounds, swaps recorded), then ``analysis_failed`` when the list stays short;
- spotdown unreachable (each reason), a provider outage, a wait that does not fit the budget or a
  spent budget: ``analysis_unavailable`` (retryable), never a list from MOWL's sections;
- the wait budget is the attempt's (reset by a retry, kept by a lease-lost requeue); the
  idempotency keys are stable across runs.

The analysis stage after ``choose`` (stage B, ``pick_analyse``) is stubbed out here (``match_only``)
so these tests see the match stage alone; ``test_spotdown_song_analysis_job`` runs both.
"""

import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from classbeat import egress_spotdown
from classbeat.db import user_session
from classbeat.songs import pick, pick_match, pick_match_batch, pick_match_budget
from classbeat.spotdown import outage_policy
from classbeat.spotdown import song_match_policy as policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.song_match_record_model import KEY
from ctx import make_context
from pickfake import call, entries, turn
from songtools import Rider, SongEnv, choose_turns, pool_turns, song_id
from spotdownfake import SERVICE_KEY, FakeSpotdown, spotdown

__all__ = ["spotdown"]  # the fixture

pytestmark = pytest.mark.usefixtures("songenv", "match_only", "waits")

# The songs ``choose_turns`` picks (pool index = song number in ``pool_turns``), with their roles.
LIST = [(0, "warmup"), (2, "build"), (4, "climb"), (10, "recovery"), (6, "sprint"),
        (7, "sprint"), (11, "recovery"), (12, "cooldown")]  # fmt: skip
POOL = list(range(14))
REAL_SLEEP = pick_match_batch._sleep  # pyright: ignore[reportPrivateUsage]  # before ``waits``
ERRORS = (OSError, ValueError, KeyError, TypeError, ZeroDivisionError, AttributeError,
          LookupError, PermissionError)  # fmt: skip


def chosen(order: list[int], songs: list[tuple[int, str]] = LIST) -> list[dict[str, Any]]:
    """The choose stage's turns for the pool shown in ``order`` (song numbers)."""
    return choose_turns([("pool", order.index(n), role) for n, role in songs])


def round_turns(refs: range = range(4, 10), role: str = "climb") -> list[dict[str, Any]]:
    """A replacement round's ``pick_pool`` stage: ``beta`` finds songs 10 to 19 (refs 0 to 9)."""
    return [
        turn(call("search_tracks", {"query": "beta"}, expect={"ok": True})),
        turn(call("submit_pool", entries(*[(r, role) for r in refs]), expect={"accepted": True})),
    ]


def script(*turns: dict[str, Any]) -> dict[str, Any]:
    return {"model": "fake-model", "turns": list(turns)}


def tag(songenv: SongEnv, status: str, *songs: int) -> None:
    for n in songs:
        songenv.fake.catalogue[song_id(n)]["name"] += f" [{status}]"


@pytest.fixture
def match_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The analysis stage (SD-P6 stage B) leaves the chosen list as it is."""

    async def chosen_as_is(_run: object, _matched: object, _map: object, chosen: Any) -> Any:
        return chosen, {}

    monkeypatch.setattr(pick, "analyse_list", chosen_as_is)


@pytest.fixture
def waits(songenv: SongEnv, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The stage's waits, made at once (each is still charged to the job's budget)."""
    made: list[float] = []

    async def sleep(_run: object, seconds: float) -> None:
        made.append(seconds)

    monkeypatch.setattr(pick_match_batch, "_sleep", sleep)
    return made


async def listed(songenv: SongEnv, rider: Rider, **extra: Any) -> tuple[UUID, Any]:
    job_id, revision_id, _ = await songenv.waiting(rider, **extra)
    return job_id, songenv.revision(revision_id)


def budget_row(songenv: SongEnv, job_id: UUID) -> Any:
    return songenv.sql(
        "SELECT spent_seconds, fallback FROM spotdown_song_budgets WHERE job_id = :j", j=job_id
    ).first()


def matches(spotdown: FakeSpotdown) -> list[dict[str, Any]]:
    return [json.loads(s.body) for s in spotdown.seen if s.path == "/v2/match"]


def keys(spotdown: FakeSpotdown) -> list[str]:
    return [s.headers["idempotency-key"] for s in spotdown.seen if s.path == "/v2/match"]


async def ended(songenv: SongEnv, rider: Rider, **extra: Any) -> tuple[UUID, str, str]:
    """One attempt of a new pick: (job id, the job's state, how the attempt ended: the category of
    its ``retry_scheduled`` or ``failed`` event)."""
    job_id = await songenv.created(rider, **extra)
    row = await songenv.run(job_id)
    return job_id, row.state, ending(songenv, job_id)


def ending(songenv: SongEnv, job_id: UUID) -> str:
    """The category of the job's latest ``retry_scheduled`` or ``failed`` event (or empty)."""
    event = songenv.sql(
        "SELECT payload ->> 'category' AS category FROM job_events WHERE job_id = :j "
        "AND kind IN ('retry_scheduled', 'failed') ORDER BY seq DESC LIMIT 1",
        j=job_id,
    ).first()
    return "" if event is None else event.category


def ids(revision: Any) -> list[str]:
    return [s["spotify_id"] for s in revision.content["songs"]]


# --- the match ------------------------------------------------------------------------------


async def test_all_matched_keeps_the_chosen_list_and_records_the_match(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    songenv.script = script(*pool_turns(), *chosen(POOL))
    job_id, revision = await listed(songenv, rider)
    assert ids(revision) == [song_id(n) for n, _ in LIST]
    record = revision.provenance[KEY]
    assert record["songs"] == [
        {"position": n, "status": "matched", "use": "timing", "band": "confident",
         "confidence": 0.92}
        for n in range(8)
    ]  # fmt: skip
    assert (record["swaps"], record["rounds"], record["missing"]) == ([], 0, 0)
    assert "youtube:" not in str(revision.provenance)
    # One request: the 14 candidates' metadata, by index, with no Spotify id.
    (body,) = matches(spotdown)
    assert [t["client_ref"] for t in body["tracks"]] == [f"t{n}" for n in range(14)]
    assert set(body["tracks"][0]) == {"client_ref", "title", "artists", "duration_s"}
    assert "cache_scope" not in body
    sent = str([s.body for s in spotdown.seen])
    assert all(song_id(n) not in sent for n in range(20))
    assert {s.path for s in spotdown.seen} == {"/v2/match"}  # nothing downloaded or analysed
    pool = songenv.fake.playlists[songenv.pool(rider.user_id).playlist_id].entries
    assert pool == [f"spotify:track:{song_id(n)}" for n in POOL]  # today's pool write
    assert tuple(budget_row(songenv, job_id)) == (0.0, False)


async def test_missing_candidates_are_left_out_while_the_spares_fill_the_list(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    tag(songenv, "not_found", 1)
    tag(songenv, "source_unavailable", 3)
    rider = await songenv.rider()
    eligible = [n for n in POOL if n not in (1, 3)]
    songenv.script = script(*pool_turns(), *chosen(eligible))  # no replacement round
    _, revision = await listed(songenv, rider)
    assert ids(revision) == [song_id(n) for n, _ in LIST]
    record = revision.provenance[KEY]
    assert (record["missing"], record["rounds"], record["swaps"]) == (2, 0, [])
    assert len(matches(spotdown)) == 1
    pool = songenv.pool(rider.user_id)
    items = songenv.fake.playlists[pool.playlist_id].entries
    assert items == [f"spotify:track:{song_id(n)}" for n in eligible]  # the missing are not written


async def test_a_short_pool_gets_a_replacement_round_with_the_swap_recorded(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    tag(songenv, "not_found", 4, 5)  # both climbs: the list lacks its climb
    rider = await songenv.rider()
    eligible = [n for n in POOL if n not in (4, 5)] + list(range(14, 20))
    songs = [(14, "climb") if n == 4 else (n, role) for n, role in LIST]
    songenv.script = script(*pool_turns(), *round_turns(), *chosen(eligible, songs))
    _, revision = await listed(songenv, rider)
    ids = [s["spotify_id"] for s in revision.content["songs"]]
    assert ids == [song_id(n) for n, _ in songs]
    record = revision.provenance[KEY]
    original = songenv.fake.catalogue[song_id(4)]
    assert record["swaps"] == [
        {
            "position": 2,
            "original": {
                "spotify_id": song_id(4),
                "title": original["name"],
                "artist": original["artist"],
            },
            "reason": "not_found",
            "round": 1,
        }
    ]
    assert (record["rounds"], record["missing"]) == (1, 2)  # songs 4 and 5 are left out
    first, second = matches(spotdown)
    assert (len(first["tracks"]), len(second["tracks"])) == (14, 6)
    assert len(set(keys(spotdown))) == 2
    assert len(revision.provenance["agent_run_ids"]) == 3  # pool, the round, choose


async def test_a_replacement_round_never_adds_a_song_that_is_excluded(
    songenv: SongEnv, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``match_pool(exclude=)`` (a composer swap's songs taken out): the round's candidate set
    is filtered, so the excluded song is never matched, never a swap, never in the list."""
    real = pick_match.match_pool

    async def excluding(*args: Any) -> Any:
        return await real(*args, exclude=frozenset({song_id(14)}))

    monkeypatch.setattr(pick, "match_pool", excluding)
    tag(songenv, "not_found", 4, 5)
    rider = await songenv.rider()
    eligible = [n for n in POOL if n not in (4, 5)] + list(range(15, 20))
    songs = [(15, "climb") if n == 4 else (n, role) for n, role in LIST]
    songenv.script = script(*pool_turns(), *round_turns(), *chosen(eligible, songs))
    _, revision = await listed(songenv, rider)
    assert song_id(14) not in ids(revision)
    first, second = matches(spotdown)
    assert (len(first["tracks"]), len(second["tracks"])) == (14, 5)  # song 14 is not matched


async def test_at_most_three_rounds_then_analysis_failed_for_a_short_list(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    gone = [0, 1, 3, 5, 8, 9, 13]  # 7 usable of 14: one song short of 8
    tag(songenv, "not_found", *gone, *range(14, 20))  # and every new candidate missing too
    rider = await songenv.rider()
    shown = [n for n in POOL if n not in gone] + gone  # the missing ones after the usable ones
    songenv.script = script(
        *pool_turns(),
        *round_turns(role="warmup"),
        *round_turns(role="warmup"),  # nothing new: every candidate was seen
        *round_turns(role="warmup"),
        *chosen(shown),
    )
    job_id, state, category = await ended(songenv, rider)
    assert (state, category) == ("failed", "analysis_failed")  # not retried, no list from MOWL
    assert songenv.row(job_id).approval_revision_id is None
    assert len(matches(spotdown)) == 2  # the pool, then round 1's new candidates


async def test_uncertain_and_a_lone_provider_error_are_missing_songs_the_spares_replace(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    tag(songenv, "uncertain", 2)
    tag(songenv, "provider_error", 6, 7)
    rider = await songenv.rider()
    eligible = [n for n in POOL if n not in (2, 6, 7)]
    songs = [(0, "warmup"), (1, "build"), (4, "climb"), (10, "recovery"), (5, "sprint"),
             (8, "sprint"), (11, "recovery"), (12, "cooldown")]  # fmt: skip
    songenv.script = script(*pool_turns(), *chosen(eligible, songs))
    job_id, revision = await listed(songenv, rider)
    assert ids(revision) == [song_id(n) for n, _ in songs]
    record = revision.provenance[KEY]
    assert {s["status"] for s in record["songs"]} == {"matched"}  # the missing are not in the list
    assert record["missing"] == 3
    # One retry of the two, after a pause charged to the job's budget.
    _, retry = matches(spotdown)
    assert [t["client_ref"] for t in retry["tracks"]] == ["t6", "t7"]
    assert tuple(budget_row(songenv, job_id)) == (policy.RETRY_PAUSE_SECONDS, False)


async def test_a_provider_error_that_clears_on_its_retry_is_matched(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    first = []
    for n in POOL:
        item: dict[str, Any] = {"schema_version": "match-v1", "client_ref": f"t{n}"}
        if n == 2:
            first.append({**item, "status": "provider_error", "alternates": []})
        else:
            best = spotdown._candidate(None, f"youtube:vid{n:08d}", 0.92)  # pyright: ignore[reportPrivateUsage]
            first.append(
                {**item, "status": "matched", "best": best, "alternates": [],
                 "matcher_version": "1"}
            )  # fmt: skip
    spotdown.inject["/v2/match"] = [httpx.Response(200, json={"results": first})]
    songenv.script = script(*pool_turns(), *chosen(POOL))
    _, revision = await listed(songenv, rider)
    assert revision.provenance[KEY]["songs"][1]["status"] == "matched"
    assert len(set(keys(spotdown))) == 2  # the retry is a new request


# --- unreachable and the failure kinds -------------------------------------------------------


def inject(spotdown: FakeSpotdown, how: str, songenv: SongEnv) -> None:
    if how == "connect_error":
        spotdown.connect_error = True
    elif how in ("503", "413", "400", "409", "401", "403", "302"):
        spotdown.inject["/v2/match"] = [httpx.Response(int(how))]
    elif how == "wrong_key":
        spotdown.key = "another-key"
    elif how == "bad_answer":
        spotdown.inject["/v2/match"] = [httpx.Response(200, json={"results": []})]
    elif how == "source_disabled":
        error = {"error": {"code": "source_disabled", "message": ""}}
        spotdown.inject["/v2/match"] = [httpx.Response(422, json=error)]
    elif how == "not_configured":
        egress_spotdown.install_spotdown(None, None)
    elif how == "outage":
        tag(songenv, "provider_error", 1, 3, 5, 8, 9, 12, 13)  # 7 of 14, after the retry


# (how, the reason it is logged with or None, the attempt's ending: (job state, category))
UNREACHABLE = [
    ("connect_error", "connection", ("queued", "analysis_unavailable")),
    ("503", "server_error", ("queued", "analysis_unavailable")),
    ("302", "blocked", ("queued", "analysis_unavailable")),  # a redirect: the egress refuses it
    ("401", "unauthorized", ("queued", "analysis_unavailable")),
    ("403", "unauthorized", ("queued", "analysis_unavailable")),
    ("wrong_key", "unauthorized", ("queued", "analysis_unavailable")),
    ("source_disabled", "source_disabled", ("queued", "analysis_unavailable")),
    ("not_configured", "not_configured", ("queued", "analysis_unavailable")),
    ("outage", None, ("queued", "analysis_unavailable")),
    ("413", "too_large", ("failed", "analysis_failed")),
    ("400", "rejected", ("failed", "analysis_failed")),
    ("bad_answer", "bad_answer", ("failed", "analysis_failed")),
    ("409", "conflict", ("failed", "internal")),
]


@pytest.mark.parametrize(("how", "reason", "ending"), UNREACHABLE, ids=[u[0] for u in UNREACHABLE])
async def test_each_unreachable_reason_ends_its_category_and_makes_no_list(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    caplog: pytest.LogCaptureFixture,
    how: str,
    reason: str | None,
    ending: tuple[str, str],
) -> None:
    rider = await songenv.rider()
    inject(spotdown, how, songenv)
    songenv.script = script(*pool_turns(), *chosen(POOL))
    with caplog.at_level(logging.INFO):
        job_id, state, category = await ended(songenv, rider)
    assert (state, category) == ending
    assert songenv.row(job_id).approval_revision_id is None  # no list, from no other timing
    if reason is not None:
        assert outage_policy.log_message(reason) in caplog.text  # type: ignore[arg-type]  # fmt: skip


async def test_a_retried_attempt_starts_with_a_fresh_budget_and_finds_spotdown_up(
    songenv: SongEnv, spotdown: FakeSpotdown, waits: list[float]
) -> None:
    tag(songenv, "rate_limited", 2)  # every read of song 2 is limited: the pass limit ends it
    rider = await songenv.rider()
    songenv.script = script(*pool_turns(), *chosen(POOL))
    job_id, state, category = await ended(songenv, rider)
    assert (state, category) == ("queued", "analysis_unavailable")
    assert len(waits) == policy.MAX_PASSES - 1  # attempt 1 spent its waits...
    row = budget_row(songenv, job_id)
    assert tuple(row) == (0.0, False)  # ...and the retry reset them
    # Attempt 2: the song is no longer limited; it runs from a budget of 0 and succeeds.
    songenv.fake.catalogue[song_id(2)]["name"] = songenv.fake.catalogue[song_id(2)]["name"].replace(
        " [rate_limited]", ""
    )
    waits.clear()
    songenv.script = script(*pool_turns(), *chosen(POOL))
    assert (await songenv.run(job_id)).state == "waiting_for_approval"
    assert waits == []
    assert tuple(budget_row(songenv, job_id)) == (0.0, False)


async def test_an_unavailable_flag_ends_the_same_attempt_before_spotdown_is_asked_again(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    """The flag is set (a lease-lost requeue keeps it): the next run of the attempt ends
    ``analysis_unavailable`` at once, sending nothing."""
    rider = await songenv.rider()
    job_id = await songenv.created(rider)
    songenv.sql(
        "INSERT INTO spotdown_song_budgets (owner_id, job_id, spent_seconds, fallback) "
        "VALUES (:o, :j, 0, true)",
        o=rider.user_id,
        j=job_id,
    )
    songenv.script = script(*pool_turns(), *chosen(POOL))
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("queued", None)
    assert spotdown.seen == []


def _raiser(error: type[BaseException]) -> Callable[..., Any]:
    def boom(*_args: object, **_kwargs: object) -> Any:
        raise error("injected")

    return boom


def _async_raiser(error: type[BaseException]) -> Callable[..., Any]:
    async def boom(*_args: object, **_kwargs: object) -> Any:
        raise error("injected")

    return boom


# (module, attribute, async, the scenario: "plain" | "short" (both climbs missing) | "limited"
# (a rate-limited song) | "round" (the round's model turns are consumed before it raises))
CALLS = [
    (SpotdownClient, "match", True, "plain"),
    (pick_match, "SpotdownClient", False, "plain"),
    (pick_match_budget, "open", True, "plain"),
    (pick_match_budget, "charge", True, "limited"),
    (pick_match_batch, "_sleep", True, "limited"),
    (pick_match_batch, "_query", False, "plain"),
    (pick_match_batch, "_key", False, "plain"),
    (pick_match_batch, "idempotency_key", False, "plain"),
    (policy, "reaction", False, "plain"),
    (policy, "track_match", False, "plain"),
    (policy, "provider_outage", False, "plain"),
    (policy, "wait_for", False, "limited"),
    (pick_match, "short", False, "short"),
    (pick_match, "to_replace", False, "short"),
    (pick_match, "run_stage", True, "short"),
    (pick_match, "pair_swaps", False, "round"),
    (pick_match, "match_record", False, "plain"),
]


@pytest.mark.parametrize(
    ("n", "target"), list(enumerate(CALLS)), ids=[f"{c[0].__name__}.{c[1]}" for c in CALLS]
)
async def test_an_error_of_any_type_in_the_stage_is_internal_never_a_list(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    n: int,
    target: tuple[Any, str, bool, str],
) -> None:
    """Nothing in the stage swallows an error (#1354): each call raising ends the attempt
    ``internal``, and no list is written."""
    owner, name, is_async, scenario = target
    if scenario in ("short", "round"):
        tag(songenv, "not_found", 4, 5)
    if scenario == "limited":
        tag(songenv, "rate_limited", 2)
    error = ERRORS[n % len(ERRORS)]
    monkeypatch.setattr(owner, name, (_async_raiser if is_async else _raiser)(error))
    rider = await songenv.rider()
    rounds = round_turns() if scenario == "round" else []
    songenv.script = script(*pool_turns(), *rounds, *chosen(POOL))
    job_id, state, category = await ended(songenv, rider)
    assert (state, category) == ("failed", "internal")
    assert songenv.row(job_id).approval_revision_id is None


# --- the budget and the keys over the job's runs ------------------------------------------------


async def test_rate_limited_waits_inside_the_attempts_budget_then_ends_unavailable(
    songenv: SongEnv, spotdown: FakeSpotdown, waits: list[float]
) -> None:
    tag(songenv, "rate_limited", 2)  # the fake answers ``retry_after_s`` 30, every time
    rider = await songenv.rider()
    songenv.script = script(*pool_turns(), {"error": "overloaded"})
    job_id = await songenv.created(rider)
    assert (await songenv.run(job_id)).state == "queued"
    passes = policy.MAX_PASSES - 1
    assert waits == [30.0] * passes  # then the pass limit: a rate-limited song is unavailable
    assert ending(songenv, job_id) == "analysis_unavailable"
    first_keys = keys(spotdown)
    assert len(set(first_keys)) == policy.MAX_PASSES  # a new pass is a new request
    assert tuple(budget_row(songenv, job_id)) == (0.0, False)  # the retry reset the budget
    # The next attempt asks with the same keys and has its own full budget of waits.
    waits.clear()
    assert (await songenv.run(job_id)).state == "queued"
    assert keys(spotdown)[len(first_keys) :] == first_keys
    assert waits == [30.0] * passes


async def test_a_run_stopped_during_a_wait_has_already_spent_it(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker shuts down during the first ``rate_limited`` wait: the real wait sees the
    shutdown and its checkpoint stops the run, and the job's budget already holds that wait."""
    tag(songenv, "rate_limited", 2)
    stopped: list[float] = []

    async def shut_down_then_wait(run: Any, seconds: float) -> None:
        stopped.append(seconds)
        run.shutdown.set()
        await REAL_SLEEP(run, seconds)

    monkeypatch.setattr(pick_match_batch, "_sleep", shut_down_then_wait)
    rider = await songenv.rider()
    songenv.script = script(*pool_turns(), *chosen(POOL))
    job_id = await songenv.created(rider)
    row = await songenv.run(job_id)
    assert row.state == "queued"  # handed back by the shutdown, not ended
    assert row.approval_revision_id is None
    assert stopped == [30.0]
    assert budget_row(songenv, job_id).spent_seconds == 30.0  # charged before the wait


async def test_a_wait_that_does_not_fit_the_budget_is_not_made_and_ends_unavailable(
    songenv: SongEnv, spotdown: FakeSpotdown, waits: list[float]
) -> None:
    tag(songenv, "rate_limited", 2)
    rider = await songenv.rider()
    job_id = await songenv.created(rider)
    songenv.sql(
        "INSERT INTO spotdown_song_budgets (owner_id, job_id, spent_seconds) VALUES (:o, :j, 580)",
        o=rider.user_id,
        j=job_id,
    )
    songenv.script = script(*pool_turns(), *chosen(POOL))
    assert (await songenv.run(job_id)).state == "queued"
    assert ending(songenv, job_id) == "analysis_unavailable"
    assert waits == []
    assert len(keys(spotdown)) == 1


async def test_a_429_for_the_whole_request_waits_and_asks_again(
    songenv: SongEnv, spotdown: FakeSpotdown, waits: list[float]
) -> None:
    spotdown.inject["/v2/match"] = [httpx.Response(429, headers={"Retry-After": "7"})]
    rider = await songenv.rider()
    songenv.script = script(*pool_turns(), *chosen(POOL))
    _, revision = await listed(songenv, rider)
    assert waits == [7.0]
    assert revision.provenance[KEY]["songs"][0]["status"] == "matched"


# --- isolation and logs -------------------------------------------------------------------------


async def test_the_budget_row_is_the_owners_alone(songenv: SongEnv, spotdown: FakeSpotdown) -> None:
    rider = await songenv.rider()
    songenv.script = script(*pool_turns(), *chosen(POOL))
    job_id, _ = await listed(songenv, rider)
    other = await songenv.rider()
    query = text("SELECT count(*) FROM spotdown_song_budgets")
    async with user_session(songenv.factory, make_context(other.user_id)) as db:
        assert (await db.execute(query)).scalar_one() == 0
    async with user_session(songenv.factory, make_context(rider.user_id)) as db:
        assert (await db.execute(query)).scalar_one() == 1
    assert budget_row(songenv, job_id) is not None


async def test_nothing_secret_or_identifying_is_logged(
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tag(songenv, "not_found", 4, 5)
    tag(songenv, "provider_error", 6)
    rider = await songenv.rider()
    eligible = [n for n in POOL if n not in (4, 5)] + list(range(14, 20))
    songs = [(14, "climb") if n == 4 else (n, role) for n, role in LIST]
    songenv.script = script(*pool_turns(), *round_turns(), *chosen(eligible, songs))
    with caplog.at_level(logging.DEBUG):
        await listed(songenv, rider)
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "spotdown song match" in logged
    for secret in (SERVICE_KEY, "youtube:", "vid0000", "Ride Song", "Made-up Band"):
        assert secret not in logged
    assert all(song_id(n) not in logged for n in range(20))


async def test_a_replace_matches_only_its_new_pool_and_records_only_the_new_song(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    job_id, revision, _ = await songenv.waiting(rider)  # the first pick matches its pool
    spotdown.seen.clear()
    class_id = str(songenv.row(job_id).class_id)
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    kept = [("kept", 0, "warmup"), ("kept", 1, "build"), ("pool", 0, "climb")]
    rest = [(2, "recovery"), (3, "sprint"), (4, "sprint"), (5, "recovery"), (6, "cooldown")]
    songenv.script = script(
        *round_turns(range(3, 9)),  # the replace's pool: songs 13 to 18
        *choose_turns(kept + [("kept", i, role) for i, role in rest]),
    )
    seed = {"song_list_revision_id": str(revision), "replace": [2]}
    _, new = await listed(songenv, rider, class_id=class_id, seed=seed)
    (body,) = matches(spotdown)
    assert len(body["tracks"]) == 6  # the kept songs are not matched again
    assert new.content["songs"][2]["spotify_id"] == song_id(13)
    assert new.provenance[KEY]["songs"] == [
        {"position": 2, "status": "matched", "use": "timing", "band": "confident",
         "confidence": 0.92}
    ]  # fmt: skip
