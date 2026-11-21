"""SD-P7a (#1304, ADR 0040 sections 5 to 9): a SwedeBeat mix analysed by spotdown, as a job on the
fakes: the fake MOWL and media endpoint (``mowlfake``, ``mediafake``), the fake spotdown
(``spotdownfake``, in process) and the real sandboxed child on generated audio for the local path.
Never the network: the autouse netguard refuses any other connection.

- off (the default), an owner not on the allowlist, or spotdown not installed: nothing is sent and
  the map, the row and the result are today's;
- on: the MOWL temporary-link fetch as always, then spotdown's lookup by sha256 in the owner's
  scope, then the streamed upload; the ``exact`` map is stored as a ``spotdown`` row with its
  provenance; no mix id, name, link or token is sent; the audio is gone after the job;
- every spotdown failure, an error of any type in each step, the budget and today's 180 s wait
  cap fall back to the local analysis, whose result is then today's;
- rows per analyser: switching off reads only the ``local`` row, a revise's ``prefer`` reads the
  row its base was designed on; a lost lease stores nothing; account deletion deletes the scope.
"""

import asyncio
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from mowl import music

from classbeat.account import erase
from classbeat.design import mix_spotdown, swedebeat_music, track_spotdown
from classbeat.design.mix_cache import fingerprint
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.spotdown import mix_config, mix_map_model, scope_outbox, studio_policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.mix_map_model import MIX_POLICY_VERSIONS, MixMap, mix_map_of
from classbeat.spotdown.scope_calc import cache_scope
from classbeat.spotdown.studio_tracks_config import TracksConfig
from ctx import make_context
from mediafake import FakeMedia
from mowlfake import FakeMowl
from optools import KIND as JOB_KIND
from optools import TOKEN_A, TOKEN_B, OpsEnv
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_mix_policy import mix_document
from test_swedebeat_fetch import (
    Harness,
    MixInput,
    account,
    add_beats,
    env,
    leftovers,
    operator_log,
    work,
)

__all__ = ["env", "operator_log", "spotdown", "work"]  # the fixtures

pytestmark = pytest.mark.usefixtures("mowl_api", "analysis_slot_by_default")
KEY = bytes(range(32))
NAME = "Planted Mix Name q9z"


class PreferHarness(Harness):
    """``test_swedebeat_fetch``'s harness, through ``open_swedebeat_mix`` with ``prefer`` (what a
    revise or refit passes: the fingerprint of the map its base was designed on)."""

    prefer: str | None = None

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("music")
            given = job_run.input
            assert isinstance(given, MixInput)
            analysis, _opened = await swedebeat_music.open_swedebeat_mix(
                job_run,
                self.ops.keyring,
                given.mix_id,
                connection_id=given.connection_id,
                client_factory=self.client,
                index_poll_seconds=0.01,
                prefer=self.prefer,
            )
            self.results.append(analysis)
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(JOB_KIND, MixInput, ("music",), run))
        return registry


@pytest.fixture
def harness(xenv: OpsEnv, mowl_api: FakeMowl) -> PreferHarness:
    return PreferHarness(xenv, mowl_api.media)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_spotdown, "pause", pause)
    yield
    mix_config.configure(None)


@pytest.fixture
def local_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each local analysis (the real sandboxed child still runs)."""
    seen: list[str] = []
    real = swedebeat_music.analyse_audio

    async def counted(run: JobRun, audio: Path, **kwargs: Any) -> Any:
        seen.append(audio.name)
        return await real(run, audio, **kwargs)

    monkeypatch.setattr(swedebeat_music, "analyse_audio", counted)
    return seen


@pytest.fixture
def hashed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The sha256 of each fetched mix the relay hashed."""
    seen: list[str] = []
    real = mix_spotdown._sha256  # pyright: ignore[reportPrivateUsage]

    def recorded(audio: Path) -> str:
        seen.append(real(audio))
        return seen[-1]

    monkeypatch.setattr(mix_spotdown, "_sha256", recorded)
    return seen


def turn_on(*owners: UUID) -> None:
    mix_config.configure(TracksConfig("allowlist", frozenset(owners), frozenset({"upload"}), KEY))


def rows(harness: Harness, owner: UUID) -> list[Any]:
    return harness.ops.sql(
        "SELECT mix_id, map, analyser, provenance, name, duration, listed_duration, playlist_id "
        "FROM swedebeat_mix_maps WHERE owner_id = :o ORDER BY analyser",
        o=owner,
    ).all()


def sent(fake: FakeSpotdown, path: str) -> list[Any]:
    return [s for s in fake.seen if s.path == path]


def result_of(harness: Harness) -> tuple[Any, ...]:
    r = harness.results[-1]
    return (r.name, r.duration, r.listed_duration, r.music_map, r.cached, r.analyser)


async def baseline(harness: PreferHarness, mowl_api: FakeMowl) -> tuple[Any, list[Any]]:
    """The result and the stored row of a local run (another owner, the same audio)."""
    owner, connection = await account(harness)
    mix = add_beats(mowl_api, NAME)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("completed", None)
    return result_of(harness), [(r.map, r.analyser, r.provenance) for r in rows(harness, owner)]


async def on_mix(
    harness: PreferHarness, mowl_api: FakeMowl, token: str = TOKEN_A
) -> tuple[UUID, ...]:
    owner, connection = await account(harness, token)
    turn_on(owner)
    return owner, connection


def mapped_of(fake: FakeSpotdown, analysis_id: str, mix: str, sha: str) -> MixMap:
    result = mix_map_of(fake.analysis(analysis_id), mix, sha, music.MAP_VERSION)
    assert isinstance(result, MixMap)
    return result


# --- off: today's analysis ----------------------------------------------------------------------


async def test_off_by_default_nothing_is_sent_and_the_map_is_local(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl, local_runs: list[str]
) -> None:
    off, off_rows = await baseline(harness, mowl_api)
    assert spotdown.seen == []
    assert len(local_runs) == 1
    assert off_rows == [(off[3], "local", None)]
    assert off[5] == "local"


async def test_an_owner_not_on_the_allowlist_or_without_spotdown_is_local(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    off, off_rows = await baseline(harness, mowl_api)
    owner, connection = await account(harness, TOKEN_A)
    turn_on(UUID(int=7))  # somebody else
    mix = add_beats(mowl_api, NAME)
    await harness.run(owner, connection, mix)
    assert result_of(harness) == off
    assert [(r.map, r.analyser, r.provenance) for r in rows(harness, owner)] == off_rows
    assert spotdown.seen == []
    assert harness.ops.sql("SELECT count(*) FROM spotdown_studio_budgets").scalar_one() == 0


async def test_on_without_spotdown_installed_is_local(
    harness: PreferHarness, mowl_api: FakeMowl, local_runs: list[str]
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("completed", None)
    assert [r.analyser for r in rows(harness, owner)] == ["local"]
    assert len(local_runs) == 1


# --- on: spotdown is the source -----------------------------------------------------------------


async def test_spotdown_gives_the_map_with_its_provenance_and_no_local_run(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    local_runs: list[str],
    hashed: list[str],
    work: Path,
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    row = await harness.run(owner, connection, mix)
    assert (row.state, row.error_category) == ("completed", None)
    assert local_runs == []
    scope = cache_scope(KEY, owner)
    [sha] = hashed
    [(held_scope, by)] = spotdown.analyses.values()
    assert (held_scope, by) == (scope, f"sha256:{sha}")
    [(analysis_id, _)] = spotdown.analyses.items()
    expected = mapped_of(spotdown, analysis_id, mix, sha)
    [stored] = rows(harness, owner)
    assert (stored.analyser, stored.map, stored.provenance) == (
        "spotdown",
        expected.music_map,
        expected.provenance,
    )
    assert stored.provenance["source"] == {"kind": "upload", "ref": None}
    assert stored.provenance["policy_versions"] == dict(MIX_POLICY_VERSIONS)
    result = harness.results[-1]
    assert (result.analyser, result.provenance, result.cached) == (
        "spotdown",
        expected.provenance,
        False,
    )
    assert result.fingerprint == fingerprint(expected.music_map)
    assert result.duration == pytest.approx(60.0)  # MOWL's indexed length, as before
    # The order: the lookup by sha256 in the owner's scope, then the upload, the start, the read.
    paths = [s.path for s in spotdown.seen]
    assert paths[:3] == ["/v2/analyses/lookup", "/v2/uploads", "/v2/analyses"]
    assert paths[-1] == f"/v2/analyses/{analysis_id}"
    [lookup] = sent(spotdown, "/v2/analyses/lookup")
    assert f"sha256={sha}" in lookup.query
    assert f"cache_scope={scope}" in lookup.query
    # Nothing names the mix, its name, the MOWL link or the token; only the upload has bytes.
    for seen in spotdown.seen:
        text = json.dumps([seen.path, seen.query, seen.headers])
        text += "" if seen.path == "/v2/uploads" else seen.body.decode("utf-8", "replace")
        for value in (mix, NAME, FakeMedia.HOST, "sig=", TOKEN_A, str(owner)):
            assert value not in text, value
    assert leftovers(work) == []  # the audio is gone


async def test_the_cached_spotdown_row_needs_no_fetch_and_no_spotdown_call(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    await harness.run(owner, connection, mix)
    first, calls, fetches = harness.results[-1], len(spotdown.seen), len(harness.media.requests)
    await harness.run(owner, connection, mix)
    again = harness.results[-1]
    assert (again.cached, again.analyser, again.music_map) == (True, "spotdown", first.music_map)
    assert again.provenance == first.provenance
    assert (len(spotdown.seen), len(harness.media.requests)) == (calls, fetches)


async def test_an_analysis_in_the_owners_scope_is_read_without_an_upload(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl, hashed: list[str]
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    # The same bytes analysed before in this owner's scope (the fake's audio is deterministic).
    other, other_connection = await account(harness, TOKEN_B)
    turn_on(owner, other)
    await harness.run(other, other_connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    [sha] = hashed
    spotdown.add_analysis(cache_scope(KEY, owner), f"sha256:{sha}")
    uploads = len(sent(spotdown, "/v2/uploads"))
    await harness.run(owner, connection, mix)
    assert len(sent(spotdown, "/v2/uploads")) == uploads  # no upload for the owner
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]


async def test_another_owners_scope_is_never_read_or_written(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    other, other_connection = await account(harness, TOKEN_B)
    turn_on(owner, other)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    await harness.run(other, other_connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    mine, theirs = cache_scope(KEY, owner), cache_scope(KEY, other)
    assert mine != theirs
    assert len(sent(spotdown, "/v2/uploads")) == 2  # the other's lookup missed in its own scope
    scopes = [s.query for s in sent(spotdown, "/v2/analyses/lookup")]
    assert f"cache_scope={mine}" in scopes[0]
    assert f"cache_scope={theirs}" in scopes[1]
    assert {s for s, _by in spotdown.analyses.values()} == {mine, theirs}


# --- every failure falls back to today's analysis -----------------------------------------------


@pytest.mark.parametrize(
    "end",
    [
        "processing_failed:decode_error",
        "processing_failed:timeout",
        "processing_failed:out_of_memory",
        "processing_failed:analysis_error",
        "provider_error",
        "source_unavailable",
        "not_found",
        "uncertain",
    ],
)
async def test_a_spotdown_failure_gives_todays_map(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl, end: str
) -> None:
    off, off_rows = await baseline(harness, mowl_api)
    spotdown.end = end
    owner, connection = await on_mix(harness, mowl_api, TOKEN_B)
    row = await harness.run(owner, connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    assert (row.state, row.error_category) == ("completed", None)
    assert result_of(harness) == off
    assert [(r.map, r.analyser, r.provenance) for r in rows(harness, owner)] == off_rows
    retried = end in ("provider_error", "processing_failed:timeout")
    assert len(sent(spotdown, "/v2/analyses")) == (2 if retried else 1)


async def test_unreachable_spotdown_falls_back_and_marks_the_jobs_outage(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    off, _rows = await baseline(harness, mowl_api)
    spotdown.connect_error = True
    owner, connection = await on_mix(harness, mowl_api, TOKEN_B)
    await harness.run(owner, connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    assert result_of(harness) == off
    assert [s.path for s in spotdown.seen] == ["/v2/analyses/lookup"]
    assert harness.ops.sql(
        "SELECT outage FROM spotdown_studio_budgets WHERE owner_id = :o", o=owner
    ).scalars().all() == [True]


async def test_an_outage_from_an_earlier_run_of_the_job_calls_nothing(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl, local_runs: list[str]
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    job_id = harness.job(owner, connection, add_beats(mowl_api, NAME))
    harness.ops.sql(
        "INSERT INTO spotdown_studio_budgets (owner_id, job_id, outage) VALUES (:o, :j, true)",
        o=owner, j=job_id,
    )  # fmt: skip
    await harness.ops.run_next()
    assert harness.ops.row(job_id).state == "completed"
    assert spotdown.seen == []
    assert len(local_runs) == 1


async def test_a_spent_budget_calls_nothing_and_is_local(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
    hashed: list[str],
) -> None:
    monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.0)
    owner, connection = await on_mix(harness, mowl_api)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert spotdown.seen == []
    assert hashed == []  # given up before the file is even read
    assert [r.analyser for r in rows(harness, owner)] == ["local"]


class FakeClock:
    """The relay's monotonic clock, moved on only by its pauses (polls and waits)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


async def test_the_wait_is_capped_at_todays_180_s_then_local(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
    local_runs: list[str],
) -> None:
    """spotdown's job would finish at 300 s: after the 180 s cap, well inside the job's 600 s
    budget. The relay gives up at the cap and the mix is analysed locally; without the cap it
    would wait on and use spotdown's map."""
    assert mix_spotdown.MIX_WAIT_SECONDS == 180.0
    assert studio_policy.WAIT_SECONDS == 600.0
    clock, finish_at = FakeClock(), 300.0
    monkeypatch.setattr(track_spotdown, "time", clock)
    monkeypatch.setattr(mix_spotdown, "time", clock)
    started: list[float] = []
    polled: list[float] = []

    async def pause(_run: object, seconds: float) -> None:
        clock.now += seconds

    monkeypatch.setattr(track_spotdown, "pause", pause)
    real_poll, real_begin = spotdown._poll, track_spotdown.begin  # pyright: ignore[reportPrivateUsage]

    async def begin(run: Any, session: Any) -> None:
        await real_begin(run, session)
        started.append(clock.now)

    def finishes_late(job_id: str) -> httpx.Response:
        polled.append(clock.now - started[0])
        job = spotdown.jobs[job_id]
        job.polls = 10 if clock.now - started[0] >= finish_at else 1  # done, else queued
        return real_poll(job_id)

    monkeypatch.setattr(track_spotdown, "begin", begin)
    monkeypatch.setattr(spotdown, "_poll", finishes_late)
    owner, connection = await on_mix(harness, mowl_api)
    row = await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert (row.state, row.error_category) == ("completed", None)
    assert [r.analyser for r in rows(harness, owner)] == ["local"]
    assert len(local_runs) == 1
    assert polled  # it was started and polled
    assert max(polled) <= mix_spotdown.MIX_WAIT_SECONDS  # never polled past the cap
    assert not any(s.path.startswith("/v2/analyses/an_") for s in spotdown.seen)  # never read


async def test_a_rate_limited_start_waits_and_keeps_the_same_upload(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    spotdown.inject["/v2/analyses"] = [httpx.Response(429, headers={"Retry-After": "2"})]
    owner, connection = await on_mix(harness, mowl_api)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert [r.analyser for r in rows(harness, owner)] == ["spotdown"]
    uploads = sent(spotdown, "/v2/uploads")
    assert len({u.headers["idempotency-key"] for u in uploads}) == 1  # the same upload, again
    starts = sent(spotdown, "/v2/analyses")
    assert len(starts) == 2
    assert len({s.headers["idempotency-key"] for s in starts}) == 1


async def test_a_document_of_other_bytes_is_never_used(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off, _rows = await baseline(harness, mowl_api)
    real = spotdown.analysis

    def other_bytes(analysis_id: str) -> dict[str, Any]:
        document = real(analysis_id)
        document["provenance"]["audio"]["sha256"] = "e" * 64
        return document

    monkeypatch.setattr(spotdown, "analysis", other_bytes)
    owner, connection = await on_mix(harness, mowl_api, TOKEN_B)
    await harness.run(owner, connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    assert result_of(harness) == off
    assert [r.analyser for r in rows(harness, owner)] == ["local"]


def _raising(exc: BaseException, generator: bool = False) -> Any:
    async def coroutine(*_args: object, **_kwargs: object) -> None:
        raise exc

    async def chunks(*_args: object, **_kwargs: object) -> Any:
        raise exc
        yield b""  # pragma: no cover - makes this an async generator

    return chunks if generator else coroutine


def _sync_raising(exc: BaseException) -> Any:
    def raising(*_args: object, **_kwargs: object) -> Any:
        raise exc

    return raising


@pytest.mark.parametrize(
    ("target", "name", "exc", "kind"),
    [
        (mix_spotdown, "_sha256", OSError("disk"), "sync"),
        (SpotdownClient, "lookup_content", OSError("socket"), "async"),
        (SpotdownClient, "upload", ValueError("bad"), "async"),
        (track_spotdown, "chunks", PermissionError("audio"), "generator"),
        (SpotdownClient, "start_analysis", KeyError("x"), "async"),
        (SpotdownClient, "poll", TypeError("x"), "async"),
        (SpotdownClient, "get_analysis", httpx.ReadError("x"), "async"),
        (mix_spotdown, "mix_map_of", AttributeError("x"), "sync"),
        (mix_spotdown, "valid_map", ZeroDivisionError(), "sync"),
        (track_spotdown.budget, "open", LookupError("x"), "async"),
        (mix_spotdown.relay, "note_outage", ArithmeticError("x"), "async"),
    ],
)
async def test_an_error_of_any_type_in_a_spotdown_step_falls_back(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
    work: Path,
    target: Any,
    name: str,
    exc: BaseException,
    kind: str,
) -> None:
    off, off_rows = await baseline(harness, mowl_api)
    if name == "note_outage":
        spotdown.connect_error = True  # the step that records the outage raises
    if kind == "sync":
        monkeypatch.setattr(target, name, _sync_raising(exc))
    else:
        monkeypatch.setattr(target, name, _raising(exc, generator=kind == "generator"))
    owner, connection = await on_mix(harness, mowl_api, TOKEN_B)
    row = await harness.run(owner, connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    assert (row.state, row.error_category) == ("completed", None)
    assert result_of(harness) == off
    assert [(r.map, r.analyser, r.provenance) for r in rows(harness, owner)] == off_rows
    assert leftovers(work) == []


async def test_a_lost_lease_while_waiting_stops_the_run_and_stores_nothing(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
    work: Path,
) -> None:
    real = SpotdownClient.poll

    async def stolen(self: SpotdownClient, job_id: str) -> Any:
        harness.ops.sql("UPDATE jobs SET lease_version = lease_version + 1 WHERE state = 'running'")
        return await real(self, job_id)

    monkeypatch.setattr(SpotdownClient, "poll", stolen)
    owner, connection = await on_mix(harness, mowl_api)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert rows(harness, owner) == []
    assert leftovers(work) == []


# --- rows per analyser: a class keeps the analysis it was designed on ---------------------------


async def test_switched_off_the_mix_is_analysed_locally_beside_the_spotdown_row(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl, local_runs: list[str]
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    await harness.run(owner, connection, mix)
    spotdown_map = harness.results[-1].music_map
    mix_config.configure(None)
    await harness.run(owner, connection, mix)
    assert (harness.results[-1].analyser, harness.results[-1].cached) == ("local", False)
    assert len(local_runs) == 1
    assert [(r.analyser, r.map == spotdown_map) for r in rows(harness, owner)] == [
        ("local", False),
        ("spotdown", True),  # untouched by the local store
    ]
    turn_on(owner)  # on again: the spotdown row is the hit before the local one
    await harness.run(owner, connection, mix)
    assert (harness.results[-1].analyser, harness.results[-1].cached) == ("spotdown", True)


async def test_a_spotdown_row_of_other_policy_versions_is_analysed_again(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    await harness.run(owner, connection, mix)
    fetches = len(harness.media.requests)
    stale = (*mix_map_model.MIX_POLICY_VERSIONS[:-1], ("mix_map_policy", "0"))
    monkeypatch.setattr(mix_map_model, "MIX_POLICY_VERSIONS", stale)
    await harness.run(owner, connection, mix)
    assert harness.results[-1].cached is False
    assert len(harness.media.requests) == fetches + 1  # a miss: fetched again
    assert len(sent(spotdown, "/v2/uploads")) == 1  # spotdown's lookup had it
    [stored] = rows(harness, owner)
    assert stored.provenance["policy_versions"] == dict(stale)


async def test_prefer_reads_the_row_the_base_was_designed_on_whatever_the_switch(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl, local_runs: list[str]
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    mix = add_beats(mowl_api, NAME)
    await harness.run(owner, connection, mix)  # the spotdown row
    designed_on = harness.results[-1].fingerprint
    mix_config.configure(None)
    await harness.run(owner, connection, mix)  # the local row beside it
    local = harness.results[-1].fingerprint
    assert local != designed_on
    calls, fetches = len(spotdown.seen), len(harness.media.requests)
    for prefer, analyser in ((designed_on, "spotdown"), (local, "local")):
        for switched_on in (False, True):
            mix_config.configure(None)
            if switched_on:
                turn_on(owner)
            harness.prefer = prefer
            await harness.run(owner, connection, mix)
            got = harness.results[-1]
            assert (got.analyser, got.cached, got.fingerprint) == (analyser, True, prefer)
    assert (len(spotdown.seen), len(harness.media.requests)) == (calls, fetches)
    assert len(local_runs) == 1


async def test_account_deletion_deletes_the_owners_scope_in_spotdown(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    other, other_connection = await account(harness, TOKEN_B)
    turn_on(owner, other)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    await harness.run(other, other_connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    mine, theirs = cache_scope(KEY, owner), cache_scope(KEY, other)
    assert {s for s, _by in spotdown.analyses.values()} == {mine, theirs}
    assert await erase.delete_row(harness.ops.factory, make_context(owner), KEY)
    assert await scope_outbox.drain(harness.ops.factory) == 1
    assert {s for s, _by in spotdown.analyses.values()} == {theirs}
    assert [s.path for s in spotdown.seen if s.method == "DELETE"] == [f"/v2/scopes/{mine}"]


async def test_after_a_fallback_the_decode_time_is_the_local_analysis_alone(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
    operator_log: io.StringIO,
) -> None:
    """ADR 0026 stage 1: ``analysis_decode_ms`` is the analysis that made the map; the time spent
    consulting spotdown before a fallback is logged apart (``spotdown_ms``)."""
    from classbeat.design.mixanalysis import Analysis

    async def slow_fallback(*_args: object) -> None:
        await asyncio.sleep(1.0)  # spotdown consulted for a second, then the fallback

    local = mix_map_of(mix_document([(0.0, 60.0, 124.0)]), "m", "c" * 64, music.MAP_VERSION)
    assert isinstance(local, MixMap)

    async def instant(*_args: object, **_kwargs: object) -> Analysis:
        return Analysis(local.music_map, None)

    monkeypatch.setattr(mix_spotdown, "analyse", slow_fallback)
    monkeypatch.setattr(swedebeat_music, "analyse_audio", instant)
    owner, connection = await on_mix(harness, mowl_api)
    row = await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert (row.state, row.error_category) == ("completed", None)
    (line,) = [
        json.loads(text)
        for text in operator_log.getvalue().splitlines()
        if "swedebeat mix analysed" in text
    ]
    assert line["analyser"] == "local"
    assert line["spotdown_ms"] >= 1000
    assert line["decode_ms"] < 500  # the local analysis only, not the relay before it
    assert row.analysis_decode_ms == line["decode_ms"]


async def test_a_map_the_table_check_refuses_falls_back(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: FakeMowl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []

    def refused(music_map: Any, version: int) -> bool:
        seen.append(version)
        return False

    monkeypatch.setattr(mix_spotdown, "valid_map", refused)
    owner, connection = await on_mix(harness, mowl_api)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert seen == [music.MAP_VERSION]
    assert [r.analyser for r in rows(harness, owner)] == ["local"]


async def test_a_rate_limit_longer_than_the_wait_left_falls_back_at_once(
    harness: PreferHarness, spotdown: FakeSpotdown, mowl_api: FakeMowl
) -> None:
    spotdown.inject["/v2/analyses"] = [httpx.Response(429, headers={"Retry-After": "3600"})]
    owner, connection = await on_mix(harness, mowl_api)
    await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert len(sent(spotdown, "/v2/analyses")) == 1  # not tried again: the wait does not fit
    assert [r.analyser for r in rows(harness, owner)] == ["local"]
