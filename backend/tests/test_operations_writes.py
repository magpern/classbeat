"""The shared seams of the MOWL class writes (ADR 0007 sections 1 and 8, issue X2): the request's
class, context and bound, the call timestamps of every attempt, ``actionable_at`` for a stale
multi-request call, and the context reaching the reconciler and ``on_succeeded``. A test-only
multi-request kind (``writetools``) against the in-process fake MOWL; never the network.
"""

import asyncio
import threading
import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from classbeat.jobs import queue
from classbeat.operations import OperationRequest, Reconciliation, request_hash, user
from classbeat.operations.kinds import (
    BOUND_KEY,
    MAX_BOUND_SECONDS,
    MAX_CONTEXT_BYTES,
    bound_of,
    context_size,
)
from classbeat.operations.protocol import operation_of
from classbeat.saving import operation as saving_operation
from ctx import make_context
from jobtools import new_class, new_user
from optools import PLAYLIST, TOKEN_A, FakeMowlAttach, OpsEnv, wait_entered
from writetools import BOUND, HASH, WAIT, WRITE, WriteEnv

OPS = "/api/external-operations"
ATTACH = "mowl_attach_spotify_playlist"


@pytest.fixture
def wenv(xenv: OpsEnv) -> WriteEnv:
    return WriteEnv(xenv)


def request(**overrides: Any) -> OperationRequest:
    values: dict[str, Any] = {
        "kind": WRITE,
        "idempotency_key": "k:save",
        "request_hash": HASH,
        "timeout_seconds": 60,
        "class_id": uuid.uuid4(),
        "bound_seconds": BOUND,
        **overrides,
    }
    return OperationRequest(**values)


def test_the_request_checks_its_class_context_and_bound() -> None:
    given: dict[str, Any] = {"baseline": [1, 2]}
    made = request(context=given)
    assert made.stored_context() == {"baseline": [1, 2], "bound_seconds": BOUND}
    assert made.lease_seconds == BOUND
    exactly = MAX_CONTEXT_BYTES - context_size({"x": "", "bound_seconds": BOUND})
    assert context_size(request(context={"x": "y" * exactly}).stored_context()) == MAX_CONTEXT_BYTES
    with pytest.raises(ValueError, match="2048"):
        request(context={"x": "y" * (exactly + 1)})
    with pytest.raises(ValueError, match="JSON"):
        request(context={"x": object()})
    with pytest.raises(ValueError, match="stored by the protocol"):
        request(context={"bound_seconds": 1})
    with pytest.raises(ValueError, match="names its class"):
        request(class_id=None)
    given["x"] = "y" * 3000  # changed after construction: still refused when stored
    with pytest.raises(ValueError, match="2048"):
        made.stored_context()
    with pytest.raises(ValueError, match="needs its bound"):
        request(bound_seconds=None)
    with pytest.raises(ValueError, match="several requests"):
        request(single_request=True)
    with pytest.raises(ValueError, match="whole timeout"):
        request(bound_seconds=30)
    # PR #172 review: reads clamp a stored bound to MAX_BOUND_SECONDS, so a request never
    # carries more (its lease would outlive the time the row waits); nor a non-finite one.
    assert request(bound_seconds=MAX_BOUND_SECONDS).lease_seconds == MAX_BOUND_SECONDS
    with pytest.raises(ValueError, match="at most MAX_BOUND_SECONDS"):
        request(bound_seconds=MAX_BOUND_SECONDS + 0.5)
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            request(bound_seconds=bad)
    attach_hash = request_hash(ATTACH, source="spotify", source_id=PLAYLIST)
    with pytest.raises(ValueError, match="no class"):
        request(kind=ATTACH, request_hash=attach_hash, bound_seconds=None)
    plain = request(kind=ATTACH, request_hash=attach_hash, class_id=None, bound_seconds=None)
    assert (plain.stored_context(), plain.lease_seconds) == ({}, 60)


def test_the_save_bound_is_capped_by_the_one_shared_constant() -> None:
    assert saving_operation.MAX_BOUND_SECONDS is MAX_BOUND_SECONDS
    assert saving_operation.bound_seconds(10_000) == MAX_BOUND_SECONDS


def test_bound_of_reads_a_stored_bound_clamped_or_not_at_all() -> None:
    """PR #172 review item 3: a stored bound is read clamped to 0..MAX_BOUND_SECONDS; a huge
    int is clamped (no ``OverflowError``), and a non-number, bool, NaN or non-positive is none."""
    assert bound_of({BOUND_KEY: 600}) == 600.0
    assert bound_of({BOUND_KEY: 600.5}) == 600.5
    for huge in (1e9, 10**400, float("inf")):
        assert bound_of({BOUND_KEY: huge}) == float(MAX_BOUND_SECONDS), huge
    for none in (float("nan"), -1, -(10**400), 0, True, False, "600", None):
        assert bound_of({BOUND_KEY: none}) is None, none
    assert bound_of({}) is None


def test_an_attach_row_cannot_name_a_class(xenv: OpsEnv) -> None:
    """#141 review: the class check is two-sided, so a class delete never cascades an attach."""
    owner = new_user(xenv.admin)
    klass = new_class(xenv.admin, owner)
    with pytest.raises(IntegrityError, match="external_operations_class"):
        xenv.sql(
            "INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, "
            "state, lease_version, class_id) VALUES (:o, :k, 'a', :h, 'pending', 1, :c)",
            o=owner, k=ATTACH, h=HASH, c=klass,
        )  # fmt: skip


async def crash_mid_call(wenv: WriteEnv, fake: FakeMowlAttach, job_id: Any) -> None:
    """The worker dies during the call; MOWL then completes what it was sent; the lease ends."""
    fake.entered.clear()
    fake.gate = threading.Event()
    execution = await wenv.claim()
    task = asyncio.ensure_future(wenv.execute(execution))
    await wait_entered(fake)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fake.gate.set()
    wenv.lose_lease(job_id)
    assert await queue.reap(wenv.ops.factory) == 1


async def test_a_crash_mid_call_waits_until_actionable_at_and_is_never_sent_again(
    wenv: WriteEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    job_id = wenv.job(owner, connection)
    await crash_mid_call(wenv, fake_attach, job_id)
    [op] = wenv.operations(owner)
    assert (op.state, op.call_ended_at, op.class_id) == ("pending", None, wenv.class_of(job_id))
    assert op.request_context == {"note": "first", "baseline": [11, 12], "bound_seconds": BOUND}
    sent = len(fake_attach.requests)

    wenv.ops.ready(job_id)
    await wenv.run_next()  # the next attempt: marked uncertain, not reconciled, nothing sent
    row = wenv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [op] = wenv.operations(owner)
    assert (op.state, op.call_ended_at) == ("uncertain", None)
    assert wenv.rec.reconciled == []
    assert len(fake_attach.requests) == sent

    ctx = make_context(owner)
    view = await user.get(wenv.ops.factory, ctx, op.id)
    assert view is not None
    assert view.actionable_at is not None
    assert (view.actionable_at - op.pending_at).total_seconds() == pytest.approx(WAIT)
    factory, keyring = wenv.ops.factory, wenv.ops.keyring
    refused = await user.do_it_anyway(factory, ctx, op.id)
    assert refused.reason == "call_may_be_running"
    refused = await user.check_again(factory, keyring, ctx, op.id, kinds=wenv.kinds)
    assert refused.reason == "call_may_be_running"
    assert wenv.rec.reconciled == []
    actor = await make_actor(owner)  # the same refusal through the API
    for action in ("check-again", "do-it-anyway"):
        answer = await actor.post(f"{OPS}/{op.id}/{action}")
        assert (answer.status_code, answer.json()) == (
            409, {"detail": {"reason": "call_may_be_running"}}
        )  # fmt: skip
    shown = (await actor.get(f"{OPS}/{op.id}")).json()
    assert shown["actionable_at"] is not None
    assert shown["class_id"] == str(op.class_id)
    assert not {"idempotency_key", "external_id", "result", "request_context"} & set(shown)

    wenv.age(op.id, WAIT + 1)  # the bound has passed: Check again reconciles, with the context
    checked = await user.check_again(factory, keyring, ctx, op.id, kinds=wenv.kinds)
    assert (checked.reason, checked.operation and checked.operation.state) == (None, "uncertain")
    [seen] = wenv.rec.reconciled
    assert seen.context["note"] == "first"
    assert seen.bound_seconds == BOUND
    assert not seen.call_returned
    decided = await user.do_it_anyway(factory, ctx, op.id)
    assert (decided.reason, decided.operation and decided.operation.state) == (None, "failed")
    assert len(fake_attach.requests) == sent  # never sent again by a job


async def test_an_uncertain_outcome_of_step_three_is_actionable_at_once(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await wenv.ops.user()
    job_id = wenv.job(owner, await wenv.ops.connect(owner))
    fake_attach.mode = "timeout_after_accept"
    await wenv.run_next()
    assert wenv.ops.row(job_id).error_category == "uncertain_write"
    [op] = wenv.operations(owner)
    assert op.state == "uncertain"
    assert op.call_ended_at >= op.pending_at
    [seen] = wenv.rec.reconciled  # step 4 ran once, at once
    assert seen.call_returned
    assert seen.context["baseline"] == [11, 12]
    assert len(fake_attach.attaches) == 1  # the first request; the second never went
    ctx = make_context(owner)
    view = await user.get(wenv.ops.factory, ctx, op.id)
    assert view is not None
    assert view.actionable_at is None
    decided = await user.do_it_anyway(wenv.ops.factory, ctx, op.id)
    assert decided.operation is not None
    assert decided.operation.state == "failed"


async def test_the_context_reaches_on_succeeded_from_the_call_the_worker_and_check_again(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    first = wenv.job(owner, connection, note="call")
    await wenv.run_next()  # the call succeeds: on_succeeded with this attempt's context
    assert wenv.ops.row(first).state == "completed"
    assert [o.context["note"] for o in wenv.rec.succeeded] == ["call"]
    assert len(fake_attach.attaches) == 2

    fake_attach.mode = "timeout_after_accept"
    wenv.rec.outcome = Reconciliation("succeeded", "4242")
    second = wenv.job(owner, connection, note="worker")
    await wenv.run_next()  # uncertain, then the worker's reconciliation finds it
    assert wenv.ops.row(second).state == "completed"
    assert [o.context["note"] for o in wenv.rec.succeeded] == ["call", "worker"]

    wenv.rec.outcome = Reconciliation("inconclusive")
    third = wenv.job(owner, connection, note="user")
    await wenv.run_next()
    assert wenv.ops.row(third).error_category == "uncertain_write"
    op = next(o for o in wenv.operations(owner) if o.state == "uncertain")
    wenv.rec.outcome = Reconciliation("succeeded", "4343")
    ctx = make_context(owner)
    checked = await user.check_again(
        wenv.ops.factory, wenv.ops.keyring, ctx, op.id, kinds=wenv.kinds
    )
    assert checked.operation is not None
    assert checked.operation.state == "succeeded"
    assert [o.context["note"] for o in wenv.rec.succeeded] == ["call", "worker", "user"]
    assert sorted(s.program_id for s in wenv.saved(owner)) == [1002, 4242, 4343]
    assert {s.class_id for s in wenv.saved(owner)} == {
        wenv.class_of(j) for j in (first, second, third)
    }


async def test_a_take_over_of_a_failed_row_starts_a_new_attempt(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    fake_attach.mode = "timeout_after_accept"
    first = wenv.job(owner, connection, key="fixed:save", note="first")
    await wenv.run_next()
    [old] = wenv.operations(owner)
    assert old.state == "uncertain"
    assert old.call_ended_at is not None
    ctx = make_context(owner)
    await user.do_it_anyway(wenv.ops.factory, ctx, old.id)
    wenv.age(old.id, 5)

    # Another class's job with the same key and hash never takes it over.
    other = wenv.job(owner, connection, key="fixed:save")
    await wenv.run_next()
    assert wenv.ops.row(other).error_category == "internal"

    fake_attach.mode = "ok"
    sent = len(fake_attach.requests)
    again = wenv.job(owner, connection, wenv.class_of(first), key="fixed:save", note="second")
    await crash_mid_call(wenv, fake_attach, again)
    [new] = wenv.operations(owner)
    assert (new.id, new.state, new.call_ended_at) == (old.id, "pending", None)
    assert new.pending_at > old.pending_at
    assert new.request_context == {"note": "second", "baseline": [11, 12], "bound_seconds": BOUND}
    assert (new.error_category, new.resolved_by, new.external_id) == (None, None, None)
    assert not operation_of(new).call_returned  # the lag rule can't use the old call_ended_at

    wenv.ops.ready(again)
    await wenv.run_next()  # the take-over's lease was lost: it waits for its own bound
    assert wenv.ops.row(again).error_category == "uncertain_write"
    [op] = wenv.operations(owner)
    assert (op.state, op.call_ended_at) == ("uncertain", None)
    assert len(wenv.rec.reconciled) == 1  # only the first attempt's step 4
    assert len(fake_attach.requests) == sent + 1  # the crashed call's first request, no more
    view = await user.get(wenv.ops.factory, ctx, op.id)
    assert view is not None
    assert view.actionable_at is not None
    assert (view.actionable_at - op.pending_at).total_seconds() == pytest.approx(WAIT)
    refused = await user.do_it_anyway(wenv.ops.factory, ctx, op.id)
    assert refused.reason == "call_may_be_running"


async def test_the_attach_has_no_bound_and_no_class(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner, TOKEN_A)
    wenv.ops.job(owner, connection)
    await wenv.ops.run_next()
    [op] = wenv.ops.operations(owner)
    assert (op.class_id, op.request_context, op.state) == (None, {}, "succeeded")
    assert op.pending_at is not None
    assert op.call_ended_at >= op.pending_at
