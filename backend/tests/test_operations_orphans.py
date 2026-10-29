"""Orphaned ``pending`` class writes, the unresolved-write rule and what a MOWL disconnect,
reconnect or ``keys wipe`` does to the class writes (ADR 0007 sections 6, 8 and 9, issue X2).
The test-only multi-request kind of ``writetools`` against the in-process fake MOWL."""

import asyncio
import threading
from typing import Any
from uuid import UUID

import pytest

from classbeat import connections
from classbeat.db import user_session
from classbeat.jobs import queue
from classbeat.operations import Reconciliation, orphans, user
from ctx import make_context
from optools import ACCOUNT_BASE, TOKEN_A, TOKEN_B, FakeMowlAttach, OpsEnv, wait_entered
from writetools import HASH, WAIT, WRITE, WriteEnv


@pytest.fixture
def wenv(xenv: OpsEnv) -> WriteEnv:
    return WriteEnv(xenv)


async def unresolved(wenv: WriteEnv, owner: UUID, class_id: UUID, job_id: UUID | None) -> bool:
    ctx = make_context(owner)
    async with user_session(wenv.ops.factory, ctx) as db:
        return await orphans.write_unresolved(db, ctx, class_id, job_id)


async def hold_mid_call(wenv: WriteEnv, fake: FakeMowlAttach) -> tuple[Any, "asyncio.Future[Any]"]:
    fake.entered.clear()
    fake.gate = threading.Event()
    execution = await wenv.claim()
    task = asyncio.ensure_future(wenv.execute(execution))
    await wait_entered(fake)
    return execution, task


@pytest.mark.parametrize("ending", ["last_attempt", "cancelled"])
async def test_an_orphaned_pending_row_becomes_uncertain_and_blocks_the_class(
    wenv: WriteEnv, fake_attach: FakeMowlAttach, ending: str
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    job_id = wenv.job(owner, connection)
    wenv.sql("UPDATE jobs SET max_attempts = 1 WHERE id = :id", id=job_id)  # the last attempt
    class_id = wenv.class_of(job_id)
    _, task = await hold_mid_call(wenv, fake_attach)
    if ending == "cancelled":
        wenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)
    wenv.lose_lease(job_id)
    assert await queue.reap(wenv.ops.factory) == 1
    row = wenv.ops.row(job_id)
    expected = ("failed", "lease_lost") if ending == "last_attempt" else ("cancelled", None)
    assert (row.state, row.error_category) == expected
    [op] = wenv.operations(owner)
    assert op.state == "pending"

    # Another job's row of a terminal job: a new Save of the class is refused (and the rule
    # resolves the orphan in the same transaction).
    assert await unresolved(wenv, owner, class_id, None)
    [op] = wenv.operations(owner)
    assert (op.state, op.error_category, op.job_id) == ("uncertain", row.error_category, job_id)
    assert await orphans.resolve_now(wenv.ops.factory, make_context(owner)) == 0  # done already

    # The fenced-out holder's late outcome cannot overwrite the marking.
    fake_attach.gate.set()  # pyright: ignore[reportOptionalMemberAccess]
    await asyncio.wait_for(task, 15)
    [op] = wenv.operations(owner)
    assert (op.state, op.external_id, op.call_ended_at) == ("uncertain", None, None)
    assert wenv.rec.succeeded == []
    assert wenv.saved(owner) == []

    ctx = make_context(owner)
    refused = await user.do_it_anyway(wenv.ops.factory, ctx, op.id)
    assert refused.reason == "call_may_be_running"  # no call_ended_at: the bound still applies
    wenv.age(op.id, WAIT + 1)
    if ending == "last_attempt":
        wenv.rec.outcome = Reconciliation("succeeded", "5150")
        checked = await user.check_again(
            wenv.ops.factory, wenv.ops.keyring, ctx, op.id, kinds=wenv.kinds
        )
        assert checked.operation is not None
        assert checked.operation.state == "succeeded"
        assert [s.program_id for s in wenv.saved(owner)] == [5150]
    else:
        decided = await user.do_it_anyway(wenv.ops.factory, ctx, op.id)
        assert decided.operation is not None
        assert decided.operation.state == "failed"
    assert not await unresolved(wenv, owner, class_id, None)  # resolved: a new Save is accepted


async def test_resolve_leaves_a_row_of_an_open_job_and_of_other_kinds_and_users(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    job_id = wenv.job(owner, connection)
    class_id = wenv.class_of(job_id)
    _, task = await hold_mid_call(wenv, fake_attach)
    # A gone job's attach row is not a class write: resolve leaves it to the attach's own path.
    wenv.sql(
        "INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
        "lease_version) VALUES (:o, 'mowl_attach_spotify_playlist', 'spotify:x', :h, "
        "'pending', 1)",
        o=owner, h=HASH,
    )  # fmt: skip
    other = await wenv.ops.user()  # another user's orphan is not this user's to resolve
    other_class = wenv.class_of(wenv.job(other, await wenv.ops.connect(other)))
    wenv.sql(
        "INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
        "lease_version, class_id, pending_at) VALUES (:o, :k, 'gone:save', :h, 'pending', 1, :c, "
        "now())",
        o=other, k=WRITE, h=HASH, c=other_class,
    )  # fmt: skip
    ctx = make_context(owner)
    assert await orphans.resolve_now(wenv.ops.factory, ctx) == 0
    states = wenv.sql(
        "SELECT owner_id = :o AS mine, kind, state FROM external_operations ORDER BY kind", o=owner
    ).all()
    assert {(r.mine, r.kind, r.state) for r in states} == {
        (True, "mowl_attach_spotify_playlist", "pending"),
        (True, WRITE, "pending"),
        (False, WRITE, "pending"),
    }
    # The holder's own row does not block its own job; it blocks any other write of the class.
    assert not await unresolved(wenv, owner, class_id, job_id)
    assert await unresolved(wenv, owner, class_id, None)
    assert not await unresolved(wenv, owner, other_class, None)  # another user's class
    fake_attach.gate.set()  # pyright: ignore[reportOptionalMemberAccess]
    await asyncio.wait_for(task, 15)
    assert wenv.ops.row(job_id).state == "completed"
    assert not await unresolved(wenv, owner, class_id, None)  # succeeded rows don't block
    assert await orphans.resolve_now(wenv.ops.factory, make_context(other)) == 1  # its job is gone


async def test_the_rule_in_a_workers_fenced_session_and_an_uncertain_row(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    fake_attach.mode = "timeout_after_accept"
    job_id = wenv.job(owner, connection)
    await wenv.run_next()
    class_id = wenv.class_of(job_id)
    [op] = wenv.operations(owner)
    assert op.state == "uncertain"
    probe = wenv.job(owner, connection, class_id, probe=True)
    await wenv.run_next()
    assert wenv.ops.row(probe).state == "completed"
    ctx = make_context(owner)
    await user.do_it_anyway(wenv.ops.factory, ctx, op.id)
    again = wenv.job(owner, connection, class_id, probe=True)
    await wenv.run_next()
    assert wenv.ops.row(again).state == "completed"
    assert wenv.rec.probes == [True, False]


async def saved_and_uncertain(wenv: WriteEnv, fake: FakeMowlAttach) -> tuple[UUID, UUID, UUID]:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner, TOKEN_A)
    saved = wenv.job(owner, connection)
    await wenv.run_next()
    assert wenv.ops.row(saved).state == "completed"
    fake.mode = "timeout_after_accept"
    wenv.job(owner, connection)
    await wenv.run_next()
    fake.mode = "ok"
    assert sorted(o.state for o in wenv.operations(owner)) == ["succeeded", "uncertain"]
    return owner, connection, wenv.class_of(saved)


@pytest.mark.parametrize("how", ["disconnect", "reconnect", "keys_wipe"])
async def test_forgetting_the_mowl_account_deletes_the_writes_and_detaches_saved_rows(
    wenv: WriteEnv, fake_attach: FakeMowlAttach, how: str
) -> None:
    owner, _, _ = await saved_and_uncertain(wenv, fake_attach)
    other, _, _ = await saved_and_uncertain(wenv, fake_attach)
    ctx = make_context(owner)
    if how == "disconnect":
        assert await connections.disconnect(wenv.ops.factory, ctx, "mowl")
    elif how == "reconnect":
        await wenv.ops.connect(owner, TOKEN_B)
    else:
        async with user_session(wenv.ops.factory, ctx) as db:
            assert await connections.delete_all_in(db, ctx) == ["mowl"]
    assert wenv.operations(owner) == []
    [kept] = wenv.saved(owner)
    assert kept.detached_at is not None
    assert kept.operation_id is None
    assert sorted(o.state for o in wenv.operations(other)) == ["succeeded", "uncertain"]
    assert [s.detached_at for s in wenv.saved(other)] == [None]


@pytest.mark.parametrize("reconnect", [False, True])
async def test_a_call_finishing_after_a_disconnect_ends_not_connected_and_saves_nothing(
    wenv: WriteEnv, fake_attach: FakeMowlAttach, reconnect: bool
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner, TOKEN_A)
    first = wenv.job(owner, connection, key="fixed:save")
    await wenv.run_next()  # account A: saved
    [old] = wenv.saved(owner)
    job_id = wenv.job(owner, connection, wenv.class_of(first))
    _, task = await hold_mid_call(wenv, fake_attach)
    ctx = make_context(owner)
    if reconnect:  # as another MOWL account
        await wenv.ops.connect(owner, TOKEN_B)
    else:
        assert await connections.disconnect(wenv.ops.factory, ctx, "mowl")
    fake_attach.gate.set()  # pyright: ignore[reportOptionalMemberAccess]
    await asyncio.wait_for(task, 15)
    row = wenv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert wenv.operations(owner) == []
    [detached] = wenv.saved(owner)
    assert (detached.id, detached.program_id) == (old.id, old.program_id)
    assert detached.detached_at is not None
    if not reconnect:
        return
    # Account switch: the same key makes a new row and a new call with B's token; nothing of
    # account A (its external_id, its saved row) is reused.
    fake_attach.gate = None
    again = wenv.job(owner, connection, wenv.class_of(first), key="fixed:save")
    await wenv.run_next()
    assert wenv.ops.row(again).state == "completed"
    [op] = wenv.operations(owner)
    assert int(op.external_id) > ACCOUNT_BASE[TOKEN_B]
    assert op.external_id != str(old.program_id)
    assert fake_attach.attaches[-1][0] == TOKEN_B
    live = [s for s in wenv.saved(owner) if s.detached_at is None]
    assert [(s.program_id, s.operation_id) for s in live] == [(int(op.external_id), op.id)]
