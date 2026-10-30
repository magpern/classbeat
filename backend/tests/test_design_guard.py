"""The connection guard of ``design_draft`` (#117 item 1, review of #119): ``hold`` at both call
sites, before the first read (``_held_link``) and in the transaction that stores the revision
(``MusicStep.still_current``), each where the link re-read alone would pass:

- a reconnect in place as account B, after which the playlist is linked again (B attached it, or
  the row is back with the same id and playlist): only ``hold``'s ``wrapped_dek`` version check
  tells the opened credentials (A's) are no longer the connection;
- a connection flipped to ``needs_reconnect`` with its link still present.

The link re-reads themselves (defence in depth: every path that deletes a link also changes the
connection row, so ``hold`` fails first) are tested by changing only the link row.

Also: a read that never ends is cut off by the step's budget, and a ``JobFailure`` raised by a
kind's ``store`` ends the job with that category (rolled back), not ``internal``.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from classbeat.design import kind as design_kind
from classbeat.design import music as design_music
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun
from designtools import DesignEnv, FakeMowlMusic
from jobcore import JobsEnv, registry_for, run_next, store_draft
from optools import PLAYLIST, TOKEN_A, TOKEN_B

pytestmark = pytest.mark.usefixtures("jenv")

B_PLAYLIST = 2001  # what the fake hands account B for its first attach


async def owner_with_mowl(denv: DesignEnv) -> tuple[uuid.UUID, uuid.UUID]:
    owner = await denv.ops.user()
    return owner, await denv.ops.connect(owner, TOKEN_A)


def relink(denv: DesignEnv, owner: uuid.UUID, playlist_id: int, link_id: Any = None) -> None:
    """The link exists again after the reconnect (B's own attach, or the same row back)."""
    denv.ops.sql(
        "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
        "VALUES (coalesce(CAST(:id AS uuid), gen_random_uuid()), :o, 'spotify', :s, :p)",
        id=None if link_id is None else str(link_id),
        o=owner,
        s=PLAYLIST,
        p=playlist_id,
    )


def flip(denv: DesignEnv, owner: uuid.UUID) -> None:
    denv.ops.sql(
        "UPDATE connections SET status = 'needs_reconnect', last_error = 'needs_reconnect' "
        "WHERE owner_id = :o",
        o=owner,
    )


Change = Callable[[DesignEnv, uuid.UUID], Awaitable[None]]


async def reconnect_and_relink_b(denv: DesignEnv, owner: uuid.UUID) -> None:
    await denv.ops.connect(owner, TOKEN_B)  # in place: the old links are deleted
    relink(denv, owner, B_PLAYLIST)


async def flipped(denv: DesignEnv, owner: uuid.UUID) -> None:
    flip(denv, owner)


@pytest.mark.parametrize("change", [reconnect_and_relink_b, flipped])
async def test_before_the_first_read_hold_stops_a_connection_that_is_no_longer_the_one_opened(
    denv: DesignEnv,
    fake_mowl: FakeMowlMusic,
    monkeypatch: pytest.MonkeyPatch,
    change: Change,
) -> None:
    owner, conn = await owner_with_mowl(denv)
    real_open = design_music.open_connection

    async def open_then_change(*args: Any, **kwargs: Any) -> Any:
        opened = await real_open(*args, **kwargs)  # account A's credentials
        await change(denv, owner)
        return opened

    monkeypatch.setattr(design_music, "open_connection", open_then_change)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert fake_mowl.attaches == [(TOKEN_A, PLAYLIST)]
    assert fake_mowl.reads == []  # neither B's playlist with A's token nor anything else
    assert fake_mowl.tokens_read == []
    assert denv.providers[0].sent == []


async def same_row_after_reconnect(denv: DesignEnv, owner: uuid.UUID) -> None:
    [link] = denv.ops.sql(
        "SELECT id, playlist_id FROM mowl_playlist_links WHERE owner_id = :o", o=owner
    ).all()
    await denv.ops.connect(owner, TOKEN_B)
    relink(denv, owner, link.playlist_id, link.id)


@pytest.mark.parametrize("change", [same_row_after_reconnect, flipped])
async def test_in_the_store_hold_stops_a_connection_that_is_no_longer_the_one_read_with(
    denv: DesignEnv,
    fake_mowl: FakeMowlMusic,
    monkeypatch: pytest.MonkeyPatch,
    change: Change,
) -> None:
    owner, conn = await owner_with_mowl(denv)
    real_run_stage = design_kind.run_stage

    async def change_then_run(*args: Any, **kwargs: Any) -> Any:
        await change(denv, owner)  # while the model designs, after the music was read
        return await real_run_stage(*args, **kwargs)

    monkeypatch.setattr(design_kind, "run_stage", change_then_run)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert denv.revisions(job_id) == []
    assert "revision_created" not in denv.kinds(job_id)
    assert [r.outcome for r in denv.agent_runs(job_id)] == ["submitted"]


async def test_a_read_that_never_ends_is_cut_off_by_the_budget(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(design_music, "MUSIC_BUDGET_SECONDS", 0.3)
    monkeypatch.setattr(design_music, "READ_MARGIN_SECONDS", 0.3)
    stuck = threading.Event()
    release = threading.Event()

    def on_read(path: str) -> None:
        if path.endswith("/AudioAnalysis") and not stuck.is_set():
            stuck.set()
            release.wait(20)  # bytes trickling in: the read never finishes on its own

    fake_mowl.on_read = on_read
    owner, conn = await owner_with_mowl(denv)
    job_id = denv.job(owner, conn)
    started = time.monotonic()
    await denv.run_next()
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 10  # the task did not wait for the read
        row = denv.ops.row(job_id)
        assert (row.state, row.attempt) == ("queued", 1)
        retry = [p for _, k, p, _ in denv.ops.events(job_id) if k == "retry_scheduled"]
        assert [r["category"] for r in retry] == ["upstream_unavailable"]
        assert denv.providers[0].sent == []
    finally:
        release.set()
    before = len(fake_mowl.reads)
    await asyncio.sleep(0.5)  # the thread reaches its next checkpoint and stops there
    assert len(fake_mowl.reads) == before


async def test_a_job_failure_from_a_store_ends_the_job_with_its_category(jenv: JobsEnv) -> None:
    async def refusing(fs: FencedSession) -> uuid.UUID | None:
        await store_draft(fs)  # written, then rolled back
        raise JobFailure("not_connected")

    async def run(job_run: JobRun) -> Complete:
        return Complete(refusing)

    _, job_id = jenv.job()
    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert jenv.counts(job_id)[1] == 0  # no revision
    assert "revision_created" not in jenv.kinds(job_id)


def drop_link(denv: DesignEnv, owner: uuid.UUID) -> None:
    """Only the link goes; the connection row (and so ``hold``) is untouched."""
    denv.ops.sql("DELETE FROM mowl_playlist_links WHERE owner_id = :o", o=owner)


def move_link(denv: DesignEnv, owner: uuid.UUID) -> None:
    denv.ops.sql(
        "UPDATE mowl_playlist_links SET playlist_id = playlist_id + 1 WHERE owner_id = :o", o=owner
    )


async def test_a_missing_link_before_the_first_read_is_not_connected_not_internal(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, conn = await owner_with_mowl(denv)
    real_open = design_music.open_connection

    async def open_then_drop(*args: Any, **kwargs: Any) -> Any:
        opened = await real_open(*args, **kwargs)
        drop_link(denv, owner)
        return opened

    monkeypatch.setattr(design_music, "open_connection", open_then_drop)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert fake_mowl.reads == []
    assert denv.providers[0].sent == []


@pytest.mark.parametrize("change", [drop_link, move_link])
async def test_in_the_store_a_link_that_is_gone_or_moved_stores_nothing(
    denv: DesignEnv,
    fake_mowl: FakeMowlMusic,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[DesignEnv, uuid.UUID], None],
) -> None:
    owner, conn = await owner_with_mowl(denv)
    real_run_stage = design_kind.run_stage

    async def change_then_run(*args: Any, **kwargs: Any) -> Any:
        change(denv, owner)  # the connection row stays as it was: hold passes
        return await real_run_stage(*args, **kwargs)

    monkeypatch.setattr(design_kind, "run_stage", change_then_run)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert denv.revisions(job_id) == []
    assert "revision_created" not in denv.kinds(job_id)


async def test_a_retryable_job_failure_from_a_store_requeues_the_job_without_a_revision(
    jenv: JobsEnv,
) -> None:
    async def unavailable(fs: FencedSession) -> uuid.UUID | None:
        await store_draft(fs)  # written, then rolled back
        raise JobFailure("upstream_unavailable")

    async def run(job_run: JobRun) -> Complete:
        return Complete(unavailable)

    _, job_id = jenv.job()
    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 1)  # the attempt is counted
    assert jenv.counts(job_id)[1] == 0  # no revision
    assert "revision_created" not in jenv.kinds(job_id)
    assert "retry_scheduled" in jenv.kinds(job_id)


@pytest.mark.parametrize("cancelled", [False, True])
async def test_the_step_is_closed_whether_the_store_ran_or_not(
    denv: DesignEnv,
    fake_mowl: FakeMowlMusic,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    """#122 item 4: the music step's credentials are dropped once the attempt is over, also when
    a cancel found by the final transaction means ``store`` never runs."""
    owner, conn = await owner_with_mowl(denv)
    steps: list[design_music.MusicStep] = []
    real_load, real_run_stage = design_kind.load_music, design_kind.run_stage

    async def keep_step(*args: Any, **kwargs: Any) -> design_music.MusicStep:
        steps.append(await real_load(*args, **kwargs))
        return steps[-1]

    async def run_then_cancel(*args: Any, **kwargs: Any) -> Any:
        outcome = await real_run_stage(*args, **kwargs)
        if cancelled:  # after the last checkpoint: only the final transaction sees it
            denv.ops.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)
        return outcome

    monkeypatch.setattr(design_kind, "load_music", keep_step)
    monkeypatch.setattr(design_kind, "run_stage", run_then_cancel)
    job_id = denv.job(owner, conn)
    await denv.run_next()
    assert denv.ops.row(job_id).state == ("cancelled" if cancelled else "completed")
    assert len(denv.revisions(job_id)) == (0 if cancelled else 1)
    [step] = steps
    assert step._opened is None  # pyright: ignore[reportPrivateUsage]
