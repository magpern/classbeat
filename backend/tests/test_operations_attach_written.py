"""``attach_written_playlist``'s re-point (#1535, #1546; ADR 0006 amendment of 2026-11-26): after
the attach (``perform``), one fenced transaction holds the connection, reads the job's own
succeeded attach row again and points the user's link at its wrapper.

- another write's attach that commits in between is undone: the link names this write's wrapper;
- a connection that is no longer the one opened (flipped, or reconnected in place) between the
  attach and the re-point ends the job ``not_connected``, and after a reconnect no link to the old
  account's wrapper survives;
- a reconnect that comes while the re-point's transaction is open waits for the share lock, so
  its forget still deletes the link;
- defence in depth: an attach row gone without a connection change (no path does that today:
  every delete of an attach row is a forget that also changes the connection) is never pointed at.

Fake MOWL only (``optools``), never the network.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.operations import attach
from classbeat.operations.protocol import Done
from optools import PLAYLIST, TOKEN_A, TOKEN_B, FakeMowlAttach, OpsEnv
from writtentools import Written

FIRST, SECOND = 1001, 1002  # the fake's wrappers for account A's first and second attach
# What the re-point's transaction waits for at most, while a reconnect queues on its share lock.
LOCK_WAIT_SECONDS = 10.0


@pytest.fixture
def written(xenv: OpsEnv, fake_attach: FakeMowlAttach) -> Written:
    return Written(xenv)


async def owner_of(xenv: OpsEnv) -> tuple[UUID, UUID]:
    owner = await xenv.user()
    return owner, await xenv.connect(owner, TOKEN_A)


def after_perform(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[Done], Awaitable[None]]
) -> None:
    """``change`` runs once the attach is recorded, before the re-point's transaction opens."""
    real = attach.perform

    async def perform_then_change(*args: Any, **kwargs: Any) -> Done:
        done = await real(*args, **kwargs)
        await change(done)
        return done

    monkeypatch.setattr(attach, "perform", perform_then_change)


async def test_another_write_attached_meanwhile_is_pointed_back_at_this_writes_wrapper(
    xenv: OpsEnv, written: Written, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M7: another job's attach of another write of the same playlist commits between this
    job's attach and its re-point (its ``on_succeeded`` moves the link to its wrapper). The
    re-point moves the link back, so the music step's ``_held_link`` finds exactly the wrapper
    this job returns."""
    owner, connection = await owner_of(xenv)
    other: list[UUID] = []

    async def another_write(_: Done) -> None:
        if not other:
            other.append(written.job(owner, connection, "write-2"))
            assert (await written.run(other[0])).state == "completed"
            assert xenv.links(owner) == [(PLAYLIST, SECOND)]

    after_perform(monkeypatch, another_write)
    row = await written.run(written.job(owner, connection, "write-1"))
    assert row.state == "completed"
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)] * 2
    assert written.playlists == [SECOND, FIRST]  # the other job ended first
    assert xenv.links(owner) == [(PLAYLIST, FIRST)]
    keys = [r.idempotency_key for r in written.attaches(owner)]
    assert len(keys) == len(set(keys)) == 2
    assert all(attach._KEY.fullmatch(k) and k.count(":") == 2 for k in keys)  # pyright: ignore[reportPrivateUsage]


async def test_a_connection_flipped_after_the_attach_is_not_connected(
    xenv: OpsEnv, written: Written, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: a flip keeps the attach row and the link (same account), so only ``hold`` tells the
    connection is no longer usable."""
    owner, connection = await owner_of(xenv)

    async def flip(_: Done) -> None:
        xenv.sql(
            "UPDATE connections SET status = 'needs_reconnect', last_error = 'needs_reconnect' "
            "WHERE owner_id = :o AND provider = 'mowl'",
            o=owner,
        )

    after_perform(monkeypatch, flip)
    row = await written.run(written.job(owner, connection, "write-1"))
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert written.playlists == []
    [op] = written.attaches(owner)
    assert op.state == "succeeded"  # the flip forgets nothing


async def test_a_reconnect_in_place_between_the_attach_and_the_re_point_leaves_no_link(
    xenv: OpsEnv, written: Written, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#117 item 1: the reconnect (maybe another MOWL account) commits after the attach was
    recorded and before the re-point: nothing points at the old account's wrapper again."""
    owner, connection = await owner_of(xenv)

    async def reconnect(_: Done) -> None:
        assert xenv.links(owner) == [(PLAYLIST, FIRST)]  # the attach's own on_succeeded
        assert await xenv.connect(owner, TOKEN_B) == connection  # in place: same row

    after_perform(monkeypatch, reconnect)
    row = await written.run(written.job(owner, connection, "write-1"))
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]
    assert written.playlists == []
    assert xenv.links(owner) == []
    assert written.attaches(owner) == []


async def test_a_reconnect_during_the_re_point_waits_for_it_and_still_forgets_the_link(
    xenv: OpsEnv, written: Written, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3 (the share lock): the reconnect starts after the re-point's transaction read the
    attach row and before it writes the link. ``hold``'s share lock makes it wait for that
    commit, so its forget deletes the link written; without the lock it would commit first and
    the upsert would recreate a link to the old account's wrapper."""
    owner, connection = await owner_of(xenv)
    performed, seen = False, list[str]()
    reconnecting: list[asyncio.Task[UUID]] = []

    async def mark(_: Done) -> None:
        nonlocal performed
        performed = True

    after_perform(monkeypatch, mark)
    real_point = attach._point_link  # pyright: ignore[reportPrivateUsage]

    async def reconnect_then_point(db: AsyncSession, *args: Any) -> None:
        if performed and not reconnecting:  # the re-point, not the attach's on_succeeded
            pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            task = asyncio.create_task(xenv.connect(owner, TOKEN_B))
            reconnecting.append(task)
            seen.append(await _blocked_by(xenv, pid, task))
        await real_point(db, *args)

    monkeypatch.setattr(attach, "_point_link", reconnect_then_point)
    row = await written.run(written.job(owner, connection, "write-1"))
    [task] = reconnecting
    assert await asyncio.wait_for(task, LOCK_WAIT_SECONDS) == connection
    assert xenv.links(owner) == []  # the forget ran after the re-point's commit
    assert written.attaches(owner) == []
    assert seen == ["blocked"]
    assert row.state == "completed"  # the music step's ``_held_link`` stops the job later


async def _blocked_by(xenv: OpsEnv, pid: int, task: asyncio.Task[UUID]) -> str:
    """``blocked`` once a lock wait of another backend names ``pid`` as its blocker, ``done``
    if ``task`` ended first (nothing waited for the transaction)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCK_WAIT_SECONDS
    while loop.time() < deadline:
        if task.done():
            return "done"
        waiting = xenv.sql(
            "SELECT count(*) FROM pg_locks WHERE NOT granted "
            "AND CAST(:pid AS integer) = ANY(pg_blocking_pids(pid))",
            pid=pid,
        ).scalar_one()
        if waiting:
            return "blocked"
        await asyncio.sleep(0.01)
    return "timeout"


async def test_an_attach_row_gone_without_a_connection_change_is_never_pointed_at(
    xenv: OpsEnv, written: Written, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M4, defence in depth: the row and the link go while the connection stays the one opened
    (no path does this today: every delete of an attach row is a forget that also changes the
    connection, so ``hold`` fails first). The re-point must not bring the link back."""
    owner, connection = await owner_of(xenv)

    async def forget_only_the_rows(done: Done) -> None:
        xenv.sql("DELETE FROM external_operations WHERE id = :id", id=done.operation_id)
        xenv.sql("DELETE FROM mowl_playlist_links WHERE owner_id = :o", o=owner)

    after_perform(monkeypatch, forget_only_the_rows)
    row = await written.run(written.job(owner, connection, "write-1"))
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert written.playlists == []
    assert xenv.links(owner) == []
