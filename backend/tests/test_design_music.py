"""The music step of ``design_draft`` (D1, #105): MOWL's BPM wait inside the lease and the
step's budget, a checkpoint before every library read (cancel, lost lease, shutdown), and a
guessed-BPM analysis never kept as shared (the user is told which tracks have no BPM yet).

Against Postgres as ``classbeat_app`` and the fake MOWL of ``designtools``; the library's poll
interval and wait are shortened for the test. Never the network.
"""

import asyncio
import uuid
from typing import Any

import pytest
from mowl import library

from agentfake import synthetic_tracks
from classbeat.connections import ConnectionFailure, run_library_call
from classbeat.design import music as design_music
from classbeat.jobs import JobFailure, RunStopped
from designtools import DesignEnv, FakeMowlMusic
from optools import TOKEN_A

pytestmark = pytest.mark.usefixtures("jenv")


@pytest.fixture
def quick_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "BPM_POLL_SEC", 0.01)
    monkeypatch.setattr(library, "BPM_WAIT_SEC", 0.3)


async def started(denv: DesignEnv) -> uuid.UUID:
    owner = await denv.ops.user()
    conn = await denv.ops.connect(owner, TOKEN_A)
    return denv.job(owner, conn)


def lease_left(denv: DesignEnv, job_id: uuid.UUID) -> float:
    return denv.ops.sql(
        "SELECT extract(epoch FROM lease_expires_at - now()) FROM jobs WHERE id = :id", id=job_id
    ).scalar_one()


@pytest.mark.usefixtures("quick_poll")
async def test_tracks_without_a_bpm_are_waited_for_then_named_in_the_revision(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    fake_mowl.bpm_never = {2, 5}
    job_id = await started(denv)
    await denv.run_next()
    assert denv.ops.row(job_id).state == "completed"
    assert fake_mowl.track_reads > 2  # the library read the tracks again while it waited
    [revision] = denv.revisions(job_id)
    assert revision.music["unknown_bpm"] == [
        {"index": 2, "title": "Invented Song 2"},
        {"index": 5, "title": "Invented Song 5"},
    ]
    # Nothing is kept: the next job on the playlist analyses (and waits) again.
    before = fake_mowl.track_reads
    row = denv.ops.row(job_id)
    second = denv.job(row.owner_id, uuid.UUID(row.input["mowl_connection_id"]))
    await denv.run_next()
    assert denv.ops.row(second).state == "completed"
    assert fake_mowl.track_reads > before + 1


@pytest.mark.usefixtures("quick_poll")
async def test_a_bpm_that_arrives_during_the_wait_leaves_no_track_unknown(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    fake_mowl.bpm_zero_reads = 2
    job_id = await started(denv)
    await denv.run_next()
    assert fake_mowl.track_reads == 3
    [revision] = denv.revisions(job_id)
    assert revision.music["unknown_bpm"] == []


async def test_the_lease_covers_the_bpm_wait_before_the_first_read(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    job_id = await started(denv)
    seen: list[float] = []

    def on_read(path: str) -> None:
        if path.endswith("/tracks") and not seen:
            seen.append(lease_left(denv, job_id))

    fake_mowl.on_read = on_read
    await denv.run_next()
    assert denv.ops.row(job_id).state == "completed"
    # The budget (the 120 s BPM wait plus the reads) and the fence's margin, not the bare lease.
    assert seen[0] > design_music.MUSIC_BUDGET_SECONDS
    assert design_music.MUSIC_BUDGET_SECONDS >= library.BPM_WAIT_SEC + 60


async def test_a_cancel_during_the_bpm_wait_stops_before_the_next_read(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "BPM_POLL_SEC", 0.01)
    monkeypatch.setattr(library, "BPM_WAIT_SEC", 60)
    fake_mowl.bpm_never = {0}
    job_id = await started(denv)

    def on_read(path: str) -> None:
        if path.endswith("/tracks") and fake_mowl.track_reads == 2:  # during the third read
            denv.ops.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)

    fake_mowl.on_read = on_read
    await denv.run_next()
    assert denv.ops.row(job_id).state == "cancelled"
    assert fake_mowl.track_reads == 3  # no read after the cancel
    assert not any("/AudioAnalysis" in r for r in fake_mowl.reads)
    assert denv.providers[0].sent == []  # no model call
    assert denv.revisions(job_id) == []
    assert denv.agent_runs(job_id) == []


async def test_a_lost_lease_during_the_wait_stops_the_reads(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "BPM_POLL_SEC", 0.01)
    monkeypatch.setattr(library, "BPM_WAIT_SEC", 60)
    fake_mowl.bpm_never = {0}
    job_id = await started(denv)

    def on_read(path: str) -> None:
        if path.endswith("/tracks") and fake_mowl.track_reads == 2:
            # What the reaper does to an expired lease: a new token, back to the queue.
            denv.ops.sql(
                "UPDATE jobs SET state = 'queued', lease_version = lease_version + 1, "
                "leased_by = NULL, lease_expires_at = NULL WHERE id = :id",
                id=job_id,
            )

    fake_mowl.on_read = on_read
    execution = await denv.run_next()
    assert execution.result == "lost"
    assert fake_mowl.track_reads == 3
    assert denv.providers[0].sent == []
    assert denv.agent_runs(job_id) == []


async def test_a_shutdown_during_the_wait_hands_the_job_back(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "BPM_POLL_SEC", 0.01)
    monkeypatch.setattr(library, "BPM_WAIT_SEC", 60)
    fake_mowl.bpm_never = {0}
    job_id = await started(denv)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_read(path: str) -> None:
        if path.endswith("/tracks") and fake_mowl.track_reads == 2:
            loop.call_soon_threadsafe(shutdown.set)

    fake_mowl.on_read = on_read
    execution = await denv.claim()
    await denv.execute(execution, shutdown)
    row = denv.ops.row(job_id)
    assert (row.state, row.attempt) == ("queued", 0)  # handed back; the attempt not counted
    assert "requeued" in denv.kinds(job_id)
    assert fake_mowl.track_reads <= 4  # stops at the next checkpoint
    assert denv.providers[0].sent == []


async def test_past_its_budget_the_step_stops_and_is_retried(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "BPM_POLL_SEC", 0.05)
    monkeypatch.setattr(library, "BPM_WAIT_SEC", 60)
    monkeypatch.setattr(design_music, "MUSIC_BUDGET_SECONDS", 0.3)
    fake_mowl.bpm_never = {0}
    job_id = await started(denv)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.attempt) == ("queued", 1)
    retry = [p for _, k, p, _ in denv.ops.events(job_id) if k == "retry_scheduled"]
    assert [r["category"] for r in retry] == ["upstream_unavailable"]
    assert fake_mowl.track_reads < 20  # well before the library's own 60 s
    assert denv.providers[0].sent == []


class _Run:
    """Just the flags ``_thread_checkpoint`` reads before it reaches the fence."""

    def __init__(self) -> None:
        self.lost = False
        self.shutdown = asyncio.Event()
        self.checked = 0

    async def checkpoint(self) -> None:
        self.checked += 1


async def test_the_thread_checkpoint_stops_on_each_flag_and_otherwise_runs_the_fence() -> None:
    loop = asyncio.get_running_loop()
    run: Any = _Run()
    now = [0.0]
    check = design_music._thread_checkpoint(run, loop, 10.0, lambda: now[0])  # pyright: ignore[reportPrivateUsage]

    def cause() -> BaseException:
        try:
            check()
        except design_music._Halt as halt:  # pyright: ignore[reportPrivateUsage]
            return halt.cause
        raise AssertionError("no stop")

    await asyncio.to_thread(check)
    assert run.checked == 1
    now[0] = 11.0
    budget = await asyncio.to_thread(cause)
    assert isinstance(budget, JobFailure)
    assert budget.category == "upstream_unavailable"
    run.shutdown.set()
    stopped = await asyncio.to_thread(cause)
    assert isinstance(stopped, RunStopped)
    assert stopped.reason == "shutdown"
    run.lost = True
    lost = await asyncio.to_thread(cause)
    assert isinstance(lost, RunStopped)
    assert lost.reason == "lost"
    assert run.checked == 1  # the flags stop it before any fence


async def test_a_stop_passes_the_connection_wrapper_as_itself() -> None:
    """Not a ConnectionFailure (which would set the connection's last_error)."""

    def call() -> None:
        raise design_music._Halt(RunStopped("cancelled"))  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(design_music._Halt):  # pyright: ignore[reportPrivateUsage]
        await run_library_call(call, sign_in=False)
    with pytest.raises(ConnectionFailure):
        await run_library_call(lambda: (_ for _ in ()).throw(ValueError("x")), sign_in=False)


def test_shareable_refuses_an_analysis_with_a_guessed_bpm() -> None:
    lib = synthetic_tracks(3)
    tracks = lib.tracks
    from mowl import spotify

    m = spotify.build_map(tracks, lib.sections)
    assert design_music.shareable({"map": m})
    tracks[1]["BPM"] = 0
    guessed = spotify.build_map(tracks, lib.sections)
    assert not design_music.shareable({"map": guessed})
    assert design_music.unknown_bpm(guessed) == (design_music.UnknownBpm(1, "Invented Song 1"),)


def test_the_fingerprint_follows_the_track_list() -> None:
    from mowl import spotify

    lib = synthetic_tracks(3)
    m = spotify.build_map(lib.tracks, lib.sections)
    same = spotify.build_map(lib.tracks, lib.sections)
    assert design_music.fingerprint(m) == design_music.fingerprint(same)
    lib.tracks[2]["Duration"] += 1000
    longer = spotify.build_map(lib.tracks, lib.sections)
    assert design_music.fingerprint(m) != design_music.fingerprint(longer)
    assert len(design_music.fingerprint(m)) == 64
