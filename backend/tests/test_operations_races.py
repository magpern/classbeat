"""External operations under races and failures around the call (ADR 0005 section 7, X1):
an unexpected exception after MOWL accepted, a row moved while it is reconciled, a stale worker
recording its outcome, a ``pending`` row whose job is gone, the lease extension of the
``pending`` write, a disconnect or reconnect between opening the connection and step 1, the
keys wipe, and ``not_applied`` only for single-request calls. Fake MOWL only (``optools``)."""

import asyncio
import threading
import uuid
from typing import Any
from urllib.error import HTTPError

import pytest
from mowl.errors import MowlError

from classbeat import connections
from classbeat.connections import ConnectionFailure, run_library_call
from classbeat.connections import repository as connection_repository
from classbeat.db import user_session
from classbeat.jobs import queue
from classbeat.operations import OPERATION_KINDS, OperationKind, Reconciliation, attach
from ctx import make_context
from optools import PLAYLIST, TOKEN_A, TOKEN_B, FakeMowlAttach, OpsEnv, wait_entered
from test_operations import ATTACH, hash_for, owner_with_job

OPS = "/api/external-operations"


async def test_an_unexpected_error_after_mowl_accepted_is_uncertain_and_never_resent(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, connection, job_id = await owner_with_job(xenv)

    async def blinked(*_: Any, **__: Any) -> bool:
        raise RuntimeError("the database blinked")  # after the attach went through

    with monkeypatch.context() as patched:  # not undo(): that would also undo the test guards
        patched.setattr(connection_repository, "record_result", blinked)
        await xenv.run_next()
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]
    row = xenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [op] = xenv.operations(owner)
    assert (op.state, op.error_category) == ("uncertain", "internal")
    again = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(again).error_category == "uncertain_write"
    assert len(fake_attach.attaches) == 1  # never sent a second time
    assert xenv.links(owner) == []


async def uncertain_op(xenv: OpsEnv, fake: FakeMowlAttach) -> tuple[uuid.UUID, uuid.UUID, Any]:
    owner, connection, _ = await owner_with_job(xenv)
    fake.mode = "timeout_after_accept"
    await xenv.run_next()
    fake.mode = "ok"
    [op] = xenv.operations(owner)
    assert op.state == "uncertain"
    return owner, connection, op


def moving_reconciler(xenv: OpsEnv, op_id: uuid.UUID, to: str) -> OperationKind:
    """A reconciler that finds the effect while someone else moves the row to ``to``."""

    async def found(_: object) -> Reconciliation:
        xenv.sql("UPDATE external_operations SET state = :s WHERE id = :id", s=to, id=op_id)
        return Reconciliation("succeeded", "555")

    return OperationKind(ATTACH, found, attach.ATTACH.on_succeeded)


@pytest.mark.parametrize("moved_to", ["failed", "pending"])
async def test_check_again_writes_only_while_the_row_is_uncertain(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any, moved_to: str
) -> None:
    owner, _, op = await uncertain_op(xenv, fake_attach)
    original = OPERATION_KINDS.replace(moving_reconciler(xenv, op.id, moved_to))
    try:
        actor = await make_actor(owner)
        response = await actor.post(f"{OPS}/{op.id}/check-again")
    finally:
        assert original is not None
        OPERATION_KINDS.replace(original)
    assert (response.status_code, response.json()["state"]) == (200, moved_to)
    [row] = xenv.operations(owner)
    assert (row.state, row.external_id, row.resolved_by) == (moved_to, None, None)
    assert xenv.links(owner) == []


async def test_the_workers_reconciliation_writes_only_while_the_row_is_uncertain(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, connection, op = await uncertain_op(xenv, fake_attach)
    original = OPERATION_KINDS.replace(moving_reconciler(xenv, op.id, "failed"))
    try:
        job_id = xenv.job(owner, connection)
        await xenv.run_next()
    finally:
        assert original is not None
        OPERATION_KINDS.replace(original)
    [row] = xenv.operations(owner)
    assert (row.state, row.external_id) == ("failed", None)
    assert xenv.links(owner) == []
    # Failed meanwhile (no effect): the attempt is retried, and the retry calls again.
    assert (xenv.row(job_id).state, xenv.row(job_id).attempt) == ("queued", 1)
    assert len(fake_attach.attaches) == 1


async def test_a_stale_worker_cannot_record_its_outcome(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    fake_attach.gate = threading.Event()
    execution = await xenv.claim()
    task = asyncio.ensure_future(xenv.execute(execution))
    await wait_entered(fake_attach)
    xenv.sql(
        "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id", id=job_id
    )
    assert await queue.reap(xenv.factory) == 1  # another worker may hold the job now
    fake_attach.gate.set()
    await asyncio.wait_for(task, 15)
    assert execution.result == "lost"
    [op] = xenv.operations(owner)
    assert (op.state, op.lease_version, op.external_id) == ("pending", 1, None)
    assert xenv.links(owner) == []
    assert xenv.row(job_id).state == "queued"
    assert [p for _, k, p, v in xenv.events(job_id) if k == "external_operation" and v == 1] == [
        {"kind": ATTACH, "state": "pending"}
    ]


async def test_a_pending_row_whose_job_is_gone_becomes_uncertain(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    xenv.sql(
        "INSERT INTO external_operations (id, owner_id, job_id, kind, idempotency_key, "
        "request_hash, state, lease_version) VALUES (gen_random_uuid(), :o, NULL, :k, :key, :h, "
        "'pending', 4)",
        o=owner, k=ATTACH, key=f"spotify:{PLAYLIST}", h=hash_for(PLAYLIST),
    )  # fmt: skip
    await xenv.run_next()
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == (
        "failed", "uncertain_write"
    )  # fmt: skip
    [op] = xenv.operations(owner)
    assert (op.state, op.job_id) == ("uncertain", None)
    assert fake_attach.requests == []


async def test_writing_pending_extends_the_lease_past_the_call(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    _, _, job_id = await owner_with_job(xenv)
    assert xenv.app.settings.job_lease_seconds == 120
    fake_attach.gate = threading.Event()
    task = asyncio.ensure_future(xenv.run_next())
    await wait_entered(fake_attach)
    left = xenv.sql(
        "SELECT extract(epoch FROM lease_expires_at - now()) FROM jobs WHERE id = :id", id=job_id
    ).scalar_one()
    fake_attach.gate.set()
    await asyncio.wait_for(task, 15)
    assert left > 140  # the attach's 120 s plus 30 s, not the 120 s lease
    assert xenv.row(job_id).state == "completed"


@pytest.mark.parametrize("reconnect", [False, True])
async def test_a_disconnect_before_step_one_sends_nothing_with_the_old_token(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch, reconnect: bool
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    real_open = attach.open_connection

    async def open_then_disconnect(*args: Any, **kwargs: Any) -> Any:
        opened = await real_open(*args, **kwargs)
        ctx = make_context(owner)
        assert await connections.disconnect(xenv.factory, ctx, "mowl")
        if reconnect:  # as another MOWL account
            await xenv.connect(owner, TOKEN_B)
        return opened

    monkeypatch.setattr(attach, "open_connection", open_then_disconnect)
    await xenv.run_next()
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == ("failed", "not_connected")
    assert fake_attach.requests == []
    assert (xenv.operations(owner), xenv.links(owner)) == ([], [])


async def test_the_keys_wipe_forgets_the_mowl_links_and_attaches(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, _ = await owner_with_job(xenv)
    await xenv.run_next()
    other, _, _ = await owner_with_job(xenv)
    await xenv.run_next()
    ctx = make_context(owner)
    async with user_session(xenv.factory, ctx) as db:
        assert await connections.delete_all_in(db, ctx) == ["mowl"]
    assert (xenv.operations(owner), xenv.links(owner)) == ([], [])
    assert len(xenv.operations(other)) == len(xenv.links(other)) == 1


def _refused_400() -> None:
    try:
        raise HTTPError("https://api.mowl.com/v1/x", 400, "x", {}, None)  # pyright: ignore[reportArgumentType]
    except HTTPError as error:
        raise MowlError("refused") from error


def _own_refusal() -> None:
    raise MowlError("MOWL's own refusal")  # no HTTP cause


@pytest.mark.parametrize("single", [True, False])
async def test_only_a_single_request_call_can_be_not_applied(single: bool) -> None:
    with pytest.raises(ConnectionFailure) as raised:
        await run_library_call(_refused_400, sign_in=False, single_request=single)
    assert raised.value.not_applied is single
    with pytest.raises(ConnectionFailure) as raised:
        await run_library_call(_own_refusal, sign_in=False)
    assert raised.value.not_applied is False  # several requests may have written before it
