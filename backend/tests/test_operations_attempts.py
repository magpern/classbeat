"""The edges of the X2 seams that the protocol's own tests leave open (#160 review): the attempt
and time checks of ``RESOLVE``, ``resolve`` before a view, the one ``actionable_at`` rule and the
view's state filter, the class check on a ``succeeded`` or ``uncertain`` row, and the context's
size measured the way Postgres measures it. A test-only
multi-request kind (``writetools``) against the in-process fake MOWL; never the network.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError

from classbeat.db import user_session
from classbeat.operations import (
    Operation,
    OperationKind,
    OperationKinds,
    OperationRequest,
    Reconciliation,
    user,
)
from classbeat.operations.kinds import (
    CALL_MAY_RUN_MARGIN_SECONDS,
    FALLBACK_BOUND_SECONDS,
    context_size,
)
from classbeat.operations.protocol import RESOLVE
from ctx import make_context
from jobtools import new_class, new_job
from optools import FakeMowlAttach, OpsEnv
from writetools import BOUND, HASH, WAIT, WRITE, WriteEnv

ATTACH = "mowl_attach_spotify_playlist"
# Escaped by jsonb ("'s, backslash, controls) or not (DEL, U+2028), and multi-byte UTF-8.
TRICKY = "é ✓ " + "".join(map(chr, [34, 92, 10, 9, 1, 0x7F, 0x2028]))


@pytest.fixture
def wenv(xenv: OpsEnv) -> WriteEnv:
    return WriteEnv(xenv)


def plant(
    wenv: WriteEnv,
    owner: UUID,
    state: str,
    *,
    kind: str = WRITE,
    class_id: UUID | None = None,
    context: dict[str, Any] | None = None,
    **more: Any,
) -> UUID:
    """An operation row written as the superuser, ``pending_at`` now, the bound in its context."""
    if kind != ATTACH and class_id is None:
        class_id = new_class(wenv.ops.admin, owner)
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "owner_id": owner,
        "kind": kind,
        "idempotency_key": f"k{uuid.uuid4()}",
        "request_hash": HASH,
        "state": state,
        "lease_version": 1,
        "class_id": class_id,
        "request_context": json.dumps({"bound_seconds": BOUND} if context is None else context),
        "pending_at": datetime.now(UTC),
        "external_id": "41" if state == "succeeded" else None,
        **more,
    }
    names = ", ".join(values)
    params = ", ".join(
        f"CAST(:{k} AS jsonb)" if k == "request_context" else f":{k}" for k in values
    )
    return wenv.sql(
        f"INSERT INTO external_operations ({names}) VALUES ({params}) RETURNING id",  # noqa: S608
        **values,
    ).scalar_one()


def state_of(wenv: WriteEnv, operation_id: UUID) -> str:
    return wenv.sql(
        "SELECT state FROM external_operations WHERE id = :i", i=operation_id
    ).scalar_one()


async def test_check_again_never_resolves_a_later_attempt(
    wenv: WriteEnv, fake_attach: FakeMowlAttach
) -> None:
    """A slow Check again reconciled attempt 1; meanwhile the row went ``failed``, was taken over
    and its new attempt went ``uncertain``. What was found for attempt 1 says nothing about
    attempt 2: ``RESOLVE`` is conditional on the ``pending_at`` that was read."""
    owner = await wenv.ops.user()
    fake_attach.mode = "timeout_after_accept"
    wenv.job(owner, await wenv.ops.connect(owner))
    await wenv.run_next()  # uncertain, call returned: actionable at once
    [op] = wenv.operations(owner)
    assert op.state == "uncertain"
    seen: list[Operation] = []

    async def reconcile(check: Any) -> Reconciliation:
        seen.append(check.operation)
        wenv.sql(  # the new attempt, also uncertain and actionable
            "UPDATE external_operations SET pending_at = now(), call_ended_at = now() "
            "WHERE id = :i",
            i=op.id,
        )
        return Reconciliation("succeeded", "4242")

    kinds = OperationKinds()
    kinds.register(OperationKind(WRITE, reconcile, None))
    ctx = make_context(owner)
    checked = await user.check_again(wenv.ops.factory, wenv.ops.keyring, ctx, op.id, kinds=kinds)
    assert len(seen) == 1
    assert checked.operation is not None
    assert checked.operation.state == "uncertain"
    assert state_of(wenv, op.id) == "uncertain"
    assert wenv.saved(owner) == []


async def test_resolve_itself_refuses_a_row_whose_call_may_still_run(wenv: WriteEnv) -> None:
    """The SQL time check of ``RESOLVE``, without the Python refusals in front of it."""
    owner = await wenv.ops.user()
    row = plant(wenv, owner, "uncertain")
    ctx = make_context(owner)
    pending_at = wenv.sql(
        "SELECT pending_at FROM external_operations WHERE id = :i", i=row
    ).scalar_one()
    params = {
        "id": row,
        "owner_id": owner,
        "state": "failed",
        "external_id": None,
        "by": "user",
        "pending_at": pending_at,
    }
    async with user_session(wenv.ops.factory, ctx) as db:
        assert (await db.execute(RESOLVE, params)).first() is None
    assert state_of(wenv, row) == "uncertain"
    wenv.age(row, WAIT + 1)
    params["pending_at"] = wenv.sql(
        "SELECT pending_at FROM external_operations WHERE id = :i", i=row
    ).scalar_one()
    async with user_session(wenv.ops.factory, ctx) as db:
        assert (await db.execute(RESOLVE, params)).first() is not None
    assert state_of(wenv, row) == "failed"


async def test_the_views_resolve_orphans_first(wenv: WriteEnv) -> None:
    """ADR 0007 section 8: an orphan shows as ``uncertain`` in the first view that reads it, with
    no action called before (``user.get`` and ``user.uncertain``)."""
    for read in ("get", "uncertain"):
        owner = await wenv.ops.user()
        klass = new_class(wenv.ops.admin, owner)
        done = new_job(
            wenv.ops.admin, owner, klass, state="completed", finished_at=datetime.now(UTC)
        )
        row = plant(wenv, owner, "pending", class_id=klass, job_id=done)
        ctx = make_context(owner)
        if read == "get":
            view = await user.get(wenv.ops.factory, ctx, row)
            assert view is not None
            assert view.state == "uncertain"
        else:
            listed = await user.uncertain(wenv.ops.factory, ctx)
            assert [v.id for v in listed] == [row]


async def test_actionable_at_is_one_rule_for_every_row(wenv: WriteEnv) -> None:
    """``requests.ACTIONABLE_AT_SQL`` through the view: the bound from the context; a class write
    with no usable bound waits the fallback (never actionable early); an attach, or a call that
    returned, waits for nothing; and a ``failed`` or ``succeeded`` row shows no time at all."""
    owner = await wenv.ops.user()
    ctx = make_context(owner)

    async def actionable(row: UUID) -> float | None:
        view = await user.get(wenv.ops.factory, ctx, row)
        assert view is not None
        if view.actionable_at is None:
            return None
        assert view.pending_at is not None
        return (view.actionable_at - view.pending_at).total_seconds()

    fallback = FALLBACK_BOUND_SECONDS + CALL_MAY_RUN_MARGIN_SECONDS
    assert await actionable(plant(wenv, owner, "uncertain")) == pytest.approx(WAIT)
    assert await actionable(plant(wenv, owner, "uncertain", context={})) == fallback
    no_number = plant(wenv, owner, "uncertain", context={"bound_seconds": "600"})
    assert await actionable(no_number) == fallback
    update = plant(wenv, owner, "uncertain", kind="mowl_update_workout", context={})
    assert await actionable(update) == fallback
    assert await actionable(plant(wenv, owner, "uncertain", kind=ATTACH, context={})) is None
    now = datetime.now(UTC)
    returned = plant(wenv, owner, "uncertain", pending_at=now, call_ended_at=now)
    assert await actionable(returned) is None
    # The SQL still gives a future time for these (no call_ended_at), the view shows none.
    for state in ("failed", "succeeded"):
        assert await actionable(plant(wenv, owner, state)) is None
    refused = await user.do_it_anyway(
        wenv.ops.factory, ctx, plant(wenv, owner, "uncertain", context={})
    )
    assert refused.reason == "call_may_be_running"


@pytest.mark.parametrize("mode", ["ok", "timeout_after_accept"])
async def test_another_class_never_reuses_a_succeeded_or_uncertain_row(
    wenv: WriteEnv, fake_attach: FakeMowlAttach, mode: str
) -> None:
    """Same key and hash, other class: the job ends ``internal`` and the row is untouched (for a
    ``failed`` row the take-over's SQL refuses it too; here only the Python check does)."""
    owner = await wenv.ops.user()
    connection = await wenv.ops.connect(owner)
    fake_attach.mode = mode
    wenv.job(owner, connection, key="fixed:save")
    await wenv.run_next()
    [before] = wenv.operations(owner)
    assert before.state == ("succeeded" if mode == "ok" else "uncertain")
    saved = len(wenv.saved(owner))
    sent = len(fake_attach.requests)
    reconciled = len(wenv.rec.reconciled)

    other = wenv.job(owner, connection, key="fixed:save")
    await wenv.run_next()
    row = wenv.ops.row(other)
    assert (row.state, row.error_category) == ("failed", "internal")
    [after] = wenv.operations(owner)
    assert (after.state, after.job_id, after.class_id) == (
        before.state,
        before.job_id,
        before.class_id,
    )
    assert len(wenv.saved(owner)) == saved
    assert len(fake_attach.requests) == sent
    assert len(wenv.rec.reconciled) == reconciled


@pytest.mark.parametrize(
    "context",
    [
        {"big": 1e16, "small": 1.5e-7, "neg": -2.5e-5, "whole": 600.0, "int": 10**30},
        {"text": TRICKY, "nested": {"a": [1, None, True, False]}},
        {"empty": {}, "list": [], "ids": ["c1", "c2"], "bound_seconds": 600.0},
    ],
)
def test_the_context_size_is_what_postgres_measures(xenv: OpsEnv, context: dict[str, Any]) -> None:
    measured = xenv.sql(
        "SELECT octet_length(CAST(:j AS jsonb)::text)", j=json.dumps(context)
    ).scalar_one()
    assert context_size(context) == measured


def test_the_context_size_refuses_what_postgres_refuses(xenv: OpsEnv) -> None:
    nul = {"text": "a" + chr(0)}
    with pytest.raises(DBAPIError, match="unsupported Unicode escape"):
        xenv.sql("SELECT CAST(:j AS jsonb)", j=json.dumps(nul))
    for refused in (nul, {"k" + chr(0): 1}, {"nan": float("nan")}, {"inf": float("inf")}):
        with pytest.raises(ValueError, match="jsonb holds no"):
            context_size(refused)
    with pytest.raises(ValueError, match="JSON"):
        OperationRequest(
            kind=WRITE,
            idempotency_key="k:save",
            request_hash=HASH,
            timeout_seconds=60,
            class_id=uuid.uuid4(),
            context=nul,
            bound_seconds=BOUND,
        )
