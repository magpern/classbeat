"""The external-operation protocol of ADR 0005 section 7, for one write made by a job.

``perform(run, request, call)`` runs the four steps through the job's ``JobRun``:

1. **Decide** (one fenced transaction, serialized on the operation row): the kind's connection
   ``guard`` first (the attach share-locks the MOWL connection it opened; gone or changed:
   ``not_connected``, no row), then ``SELECT ... FOR UPDATE`` on ``(owner_id, kind, key)``.
   No row: ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` as ``pending`` (no row back
   means another job got there first: re-load and decide again). ``succeeded`` with the same
   hash: reuse it, no call. Another hash: ``internal``. ``pending`` held by another job still
   ``running`` under that row's ``lease_version``: ``operation_busy`` (the runner requeues
   without an attempt). Any other
   ``pending`` (its holder died or lost its lease mid-call): marked ``uncertain`` with a
   conditional ``UPDATE ... WHERE state = 'pending' AND job_id = :old AND lease_version =
   :old`` that also sets ``job_id`` to this job, then step 4. ``uncertain``: its ``job_id``
   becomes this job (the row follows the job that fails on it, #125), then step 4.
   ``failed``: taken over with ``UPDATE ... WHERE state = 'failed'``. Writing ``pending``
   also extends the lease past the call's timeout (or its total bound, below). **Every
   transition into ``pending``** (the insert and the take-over) sets ``pending_at = now()``,
   ``call_ended_at = NULL`` and this attempt's ``request_context``, so nothing of an earlier
   attempt survives (ADR 0007 section 1). A row carries its class (``class_id``) exactly when
   its kind is a class write; a request for another class than the row's is ``internal``.
2. **The call**, outside any transaction (``JobRun.external_call``: no call starts after a
   cancel or SIGTERM, and once started a cancel does not interrupt it).
3. **Record** (fenced, conditional on ``state = 'pending' AND job_id AND lease_version``):
   ``succeeded``, ``failed`` (``ConnectionFailure.not_applied``: refused, or never left; or
   ``NotWritten``: the call proved nothing was written, or read back a definite other state) or
   ``uncertain``, and ``call_ended_at = now()`` with any of them ("the call returned" is
   ``call_ended_at >= pending_at``). A lost fence leaves the row ``pending`` under the old
   token, without ``call_ended_at``. A row that is
   gone (a MOWL disconnect deleted it during the call) writes nothing and ends the job
   ``not_connected``.
4. **Uncertain**: one reconciliation through the kind's ``Reconciler``. ``succeeded`` is
   reused; ``failed`` (proof of no effect) fails the attempt as retryable, so the next attempt
   takes the ``failed`` row over and calls again; otherwise the row stays ``uncertain`` and the
   job fails ``uncertain_write``. An uncertain write is never sent again by a job: the user
   decides (**Check again** or **Do it anyway**, ``classbeat.operations.user``).
   **A stale call may still be running** (ADR 0007 section 8): for a multi-request write
   (``bound_seconds`` set; ``kinds.MULTI_REQUEST_KINDS``, the MOWL class writes and the Spotify
   replace) whose call has not returned, nothing is reconciled before
   ``actionable_at = pending_at + bound_seconds + 30 s``; the job fails ``uncertain_write``
   without a read, and the user's Check again after that time does the reconciliation.

``perform_for`` hands the call the id of the row it writes for: a kind whose request is
``with_ref`` stores ``ref`` (12 hex characters of that id) in each attempt's context, and the
call puts it into what it writes (the Spotify create's description).

Every write the worker makes here goes through ``JobRun.fenced`` and records an
``external_operation {kind, state}`` event. The transactions are never nested (a second fenced
transaction inside one would wait for the fence's own row lock, #109). Only categories and
exception type names are logged, never texts.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text

from classbeat.connections import ConnectionFailure
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked, RequestBudgetExhausted
from classbeat.jobs import FencedSession, JobFailure, JobRun, failure_of
from classbeat.operations.kinds import (
    MAX_EXTERNAL_ID,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    OperationKinds,
    ReconcileInput,
    Reconciliation,
)
from classbeat.operations.requests import NOT_ACTIONABLE_SQL, OperationRequest

logger = logging.getLogger(__name__)

DECIDE_ROUNDS: Final = 3  # re-loads after a lost insert or take-over race; more is a bug
# A reconciliation proved the write had no effect: the attempt is retried (with backoff), and
# the next one takes the ``failed`` row over (ADR 0005 section 4's closest retryable category).
NO_EFFECT_CATEGORY: Final = "upstream_unavailable"


_COLUMNS = (
    "id, kind, idempotency_key, state, request_hash, external_id, result, job_id, lease_version, "
    f"class_id, request_context, pending_at, call_ended_at, {NOT_ACTIONABLE_SQL} AS waiting"
)
_LOCK = text(
    f"SELECT {_COLUMNS} FROM external_operations "  # noqa: S608 - constant column list
    "WHERE owner_id = :owner_id AND kind = :kind AND idempotency_key = :key FOR UPDATE"
)
_BY_ID = text(
    f"SELECT {_COLUMNS} FROM external_operations "  # noqa: S608 - constant column list
    "WHERE id = :id AND owner_id = :owner_id"
)
_INSERT = text(
    "INSERT INTO external_operations (id, owner_id, job_id, kind, idempotency_key, request_hash, "
    "state, lease_version, class_id, request_context, pending_at, call_ended_at) "
    "VALUES (:id, :owner_id, :job_id, :kind, :key, :hash, 'pending', :v, :class_id, "
    "CAST(:context AS jsonb), now(), NULL) "
    "ON CONFLICT (owner_id, kind, idempotency_key) DO NOTHING RETURNING id"
)
_HOLDER_RUNNING = text(
    "SELECT 1 FROM jobs WHERE id = :holder AND owner_id = :owner_id AND state = 'running' "
    "AND lease_version = :holder_v"
)
# A new attempt: nothing of the earlier one survives (its context, its call timestamps).
_TAKE_FAILED = text(
    "UPDATE external_operations SET state = 'pending', job_id = :job_id, lease_version = :v, "
    "request_context = CAST(:context AS jsonb), pending_at = now(), call_ended_at = NULL, "
    "external_id = NULL, result = '{}'::jsonb, "
    "error_category = NULL, resolved_by = NULL, resolved_at = NULL, updated_at = now() "
    "WHERE id = :id AND owner_id = :owner_id AND state = 'failed' "
    "AND class_id IS NOT DISTINCT FROM :class_id RETURNING id"
)
# An ``uncertain`` row follows the job that meets it (#125): its ``job_id`` becomes that job, so
# the job that fails ``uncertain_write`` lists it for the user's decision. ``job_id`` of an
# ``uncertain`` row is no holder: only a ``pending`` row's ``(job_id, lease_version)`` is one,
# and ``lease_version`` keeps the token of the call that may have taken effect.
_MARK_UNCERTAIN = text(
    "UPDATE external_operations SET state = 'uncertain', job_id = :job_id, updated_at = now() "
    "WHERE id = :id AND owner_id = :owner_id AND state = 'pending' "
    "AND job_id IS NOT DISTINCT FROM :old_job AND lease_version = :old_v RETURNING id"
)
_ADOPT_UNCERTAIN = text(
    "UPDATE external_operations SET job_id = :job_id, updated_at = now() "
    "WHERE id = :id AND owner_id = :owner_id AND kind = :kind AND idempotency_key = :key "
    "AND state = 'uncertain' RETURNING id"
)
_RECORD = text(
    "UPDATE external_operations SET state = :state, external_id = :external_id, "
    "result = CAST(:result AS jsonb), error_category = :category, call_ended_at = now(), "
    "resolved_by = CASE WHEN :state = 'uncertain' THEN NULL ELSE 'worker' END, "
    "resolved_at = CASE WHEN :state = 'uncertain' THEN NULL ELSE now() END, updated_at = now() "
    "WHERE id = :id AND owner_id = :owner_id AND state = 'pending' AND job_id = :job_id "
    "AND lease_version = :v RETURNING id"
)
# Step 4's outcome and the user's Check again / Do it anyway: only an ``uncertain`` row moves,
# only the attempt that was read (``pending_at``: a row that went through ``failed`` and a new
# ``pending`` meanwhile is another attempt), and never while its stale call may still be running.
RESOLVE = text(
    "UPDATE external_operations SET state = :state, "  # noqa: S608 - constant expressions
    "external_id = coalesce(:external_id, external_id), resolved_by = :by, resolved_at = now(), "
    "updated_at = now() "
    "WHERE id = :id AND owner_id = :owner_id AND state = 'uncertain' "
    "AND pending_at IS NOT DISTINCT FROM :pending_at "
    f"AND NOT {NOT_ACTIONABLE_SQL} RETURNING id"
)


@dataclass(frozen=True, slots=True)
class CallResult:
    """What a successful call gives back: the provider's id of what it wrote, and a small
    non-secret ``result`` (at most 4096 bytes as JSON, the table's check)."""

    external_id: str
    result: Mapping[str, Any] = field(default_factory=dict[str, Any])


class UncertainAnswer(Exception):
    """The provider answered, but not in a shape the kind can read: the write may have taken
    effect, so it is ``uncertain``. Carries no text."""

    def __init__(self) -> None:
        super().__init__("uncertain answer")


class NotWritten(Exception):
    """The call proved that nothing was written (a Spotify owner check that refused before any
    write request, ADR 0008 section 2) or found a definite state that differs (a read-back that
    is not the list written: ``playlist_mismatch``): ``failed`` with ``category``. Carries only
    the category."""

    def __init__(self, category: str) -> None:
        self.failure = JobFailure(category)
        super().__init__(self.failure.category)


@dataclass(frozen=True, slots=True)
class Done:
    """A ``succeeded`` operation: made by this call, found by a reconciliation, or reused."""

    operation_id: UUID
    external_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class _Call:
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class _Uncertain:
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class _Outcome:
    state: str  # succeeded, failed, uncertain
    external_id: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict[str, Any])
    failure: JobFailure | None = None


def operation_of(row: Any) -> Operation:
    return Operation(
        id=row.id,
        kind=row.kind,
        idempotency_key=row.idempotency_key,
        state=row.state,
        external_id=row.external_id,
        job_id=row.job_id,
        result=dict(row.result or {}),
        class_id=row.class_id,
        context=dict(row.request_context or {}),
        pending_at=row.pending_at,
        call_ended_at=row.call_ended_at,
    )


async def _event(fs: FencedSession, kind: str, state: str) -> None:
    await fs.event("external_operation", {"kind": kind, "state": state})


async def succeeded(db: Any, kind: OperationKind, owner_id: UUID, row: Operation) -> None:
    """What the kind writes with a ``succeeded`` row (the attach's link), same transaction."""
    if kind.on_succeeded is not None and row.external_id is not None:
        await kind.on_succeeded(db, owner_id, row)


async def _decide(
    run: JobRun, request: OperationRequest, kind: OperationKind
) -> Done | _Call | _Uncertain:
    lease = run.lease
    async with run.fenced() as fs:
        if request.guard is not None and not await request.guard(fs.db):
            raise JobFailure("not_connected")
        keyed = {"owner_id": lease.owner_id, "kind": request.kind, "key": request.idempotency_key}

        def attempt(operation_id: UUID) -> dict[str, Any]:
            # This attempt's context (with the row's own ``ref`` when the kind asks for one).
            context = request.stored_context(operation_id)
            return {"context": json.dumps(context), "class_id": request.class_id}

        for _ in range(DECIDE_ROUNDS):
            row = (await fs.db.execute(_LOCK, keyed)).first()
            if row is None:
                operation_id = uuid.uuid4()
                inserted = (
                    await fs.db.execute(
                        _INSERT,
                        {
                            **keyed,
                            **attempt(operation_id),
                            "id": operation_id,
                            "job_id": lease.job_id,
                            "hash": request.request_hash,
                            "v": lease.lease_version,
                        },
                    )
                ).first()
                if inserted is None:
                    continue  # another job inserted it first: re-load and decide again
                await fs.extend_lease_for_call(request.lease_seconds)
                await _event(fs, request.kind, "pending")
                return _Call(operation_id)
            if bytes(row.request_hash) != request.request_hash or row.class_id != request.class_id:
                logger.warning("external operation hash mismatch", extra={"kind": request.kind})
                raise JobFailure("internal")
            ids = {"id": row.id, "owner_id": lease.owner_id}
            if row.state == "succeeded":
                operation = operation_of(row)
                await succeeded(fs.db, kind, lease.owner_id, operation)  # idempotent
                return Done(row.id, str(row.external_id), reused=True)
            if row.state == "failed":
                taken = await fs.db.execute(
                    _TAKE_FAILED,
                    {**ids, **attempt(row.id), "job_id": lease.job_id, "v": lease.lease_version},
                )
                if taken.first() is None:
                    continue
                await fs.extend_lease_for_call(request.lease_seconds)
                await _event(fs, request.kind, "pending")
                return _Call(row.id)
            if row.state == "pending":
                ours = row.job_id == lease.job_id and row.lease_version == lease.lease_version
                if not ours and row.job_id is not None:
                    holder = await fs.db.execute(
                        _HOLDER_RUNNING,
                        {
                            "holder": row.job_id,
                            "owner_id": lease.owner_id,
                            "holder_v": row.lease_version,
                        },
                    )
                    if holder.first() is not None:
                        raise JobFailure("operation_busy")
                marked = await fs.db.execute(
                    _MARK_UNCERTAIN,
                    {
                        **ids,
                        "job_id": lease.job_id,
                        "old_job": row.job_id,
                        "old_v": row.lease_version,
                    },
                )
                if marked.first() is None:
                    continue
                await _event(fs, request.kind, "uncertain")
                return _Uncertain(row.id)
            # Uncertain already: it now follows this job, which fails on it (#125).
            adopted = await fs.db.execute(
                _ADOPT_UNCERTAIN, {**ids, **keyed, "job_id": lease.job_id}
            )
            if adopted.first() is None:
                continue
            await _event(fs, request.kind, "uncertain")
            return _Uncertain(row.id)
    logger.warning("external operation kept changing", extra={"kind": request.kind})
    raise JobFailure("internal")


def _checked(answer: object) -> CallResult:
    if not isinstance(answer, CallResult):
        raise UncertainAnswer
    if not answer.external_id or len(answer.external_id) > MAX_EXTERNAL_ID:
        raise UncertainAnswer
    return answer


async def _invoke(
    call: Callable[[UUID], Awaitable[CallResult]],
    operation_id: UUID,
    kind: str,
    request: OperationRequest,
) -> _Outcome:
    """Step 2. A ``ConnectionFailure`` is ``failed`` only when it proves there was no effect."""
    try:
        answer = _checked(await call(operation_id))
    except NotWritten as refused:
        return _Outcome("failed", failure=refused.failure)
    except ConnectionFailure as failure:
        state = "failed" if failure.not_applied else "uncertain"
        return _Outcome(state, failure=failure_of(failure))
    except OutboundBlocked as refused:
        # ``failed`` only when the one request was refused before it left; a refusal on a
        # redirect hop comes after the provider got the request (ADR 0005 section 7).
        never_sent = request.single_request and not refused.after_request
        state = "failed" if never_sent else "uncertain"
        return _Outcome(state, failure=JobFailure("outbound_blocked"))
    except RequestBudgetExhausted:
        # The request over the job's budget was never sent; an earlier one of a multi-request
        # write may have been, so only a single-request operation is ``failed`` for certain.
        state = "failed" if request.single_request else "uncertain"
        return _Outcome(state, failure=JobFailure("limit_exceeded"))
    except UncertainAnswer:
        logger.warning("external operation answer unreadable", extra={"kind": kind})
        return _Outcome("uncertain", failure=JobFailure("upstream_unavailable"))
    except Exception as exc:  # anything else: it may have been sent, so never call again
        logger.warning(
            "external operation call raised",
            extra={"kind": kind, "error_type": type(exc).__name__},
        )
        return _Outcome("uncertain", failure=JobFailure("internal"))
    return _Outcome("succeeded", answer.external_id, dict(answer.result))


async def _record(
    run: JobRun, kind: OperationKind, operation_id: UUID, outcome: _Outcome
) -> Done | None:
    """Step 3. Returns ``Done`` for ``succeeded``, else ``None``."""
    lease = run.lease
    category = None if outcome.failure is None else outcome.failure.category
    async with run.fenced() as fs:
        updated = await fs.db.execute(
            _RECORD,
            {
                **lease.params(),
                "id": operation_id,
                "state": outcome.state,
                "external_id": outcome.external_id,
                "result": json.dumps(dict(outcome.result)),
                "category": category,
            },
        )
        if updated.first() is None:
            row = (
                await fs.db.execute(_BY_ID, {"id": operation_id, "owner_id": lease.owner_id})
            ).first()
            if row is None:  # deleted by a MOWL disconnect during the call: no link
                logger.warning("external operation gone during the call", extra={"kind": kind.name})
                raise JobFailure("not_connected")
            logger.warning("external operation moved during the call", extra={"kind": kind.name})
            raise JobFailure("internal")
        if outcome.state == "succeeded":
            row = (
                await fs.db.execute(_BY_ID, {"id": operation_id, "owner_id": lease.owner_id})
            ).one()
            await succeeded(fs.db, kind, lease.owner_id, operation_of(row))
        await _event(fs, kind.name, outcome.state)
    if outcome.state == "succeeded":
        assert outcome.external_id is not None  # noqa: S101 - set for succeeded
        return Done(operation_id, outcome.external_id, reused=False)
    return None


async def reconcile(kind: OperationKind, check: ReconcileInput) -> Reconciliation:
    """The kind's reconciler; anything it raises is inconclusive (never its text)."""
    try:
        found = await kind.reconcile(check)
    except Exception as exc:
        logger.warning(
            "reconciliation raised", extra={"kind": kind.name, "error_type": type(exc).__name__}
        )
        return Reconciliation("inconclusive")
    if not isinstance(found, Reconciliation):
        return Reconciliation("inconclusive")
    return found


async def _reconcile_once(
    run: JobRun, kind: OperationKind, operation_id: UUID, keyring: KeyRing | None
) -> Done:
    """Step 4, once per job that meets the uncertain row; not while a stale call may still be
    running (ADR 0007 section 8): the row stays ``uncertain`` for the user and nothing is read."""
    lease = run.lease
    await run.checkpoint()  # a read-only call is next
    async with run.fenced() as fs:
        row = (
            await fs.db.execute(_BY_ID, {"id": operation_id, "owner_id": lease.owner_id})
        ).first()
    if row is None:
        raise JobFailure("not_connected")
    if row.waiting:
        raise JobFailure("uncertain_write")
    read = operation_of(row)
    found = await reconcile(kind, ReconcileInput(run.factory, keyring, run.ctx, read))
    if found.outcome == "inconclusive":
        raise JobFailure("uncertain_write")
    ids = {"id": operation_id, "owner_id": lease.owner_id}
    async with run.fenced() as fs:
        moved = await fs.db.execute(
            RESOLVE,
            {
                **ids,
                "state": found.outcome,
                "external_id": found.external_id,
                "by": "worker",
                "pending_at": read.pending_at,
            },
        )
        resolved_here = moved.first() is not None
        now = (await fs.db.execute(_BY_ID, ids)).first()
        if now is None:
            raise JobFailure("not_connected")
        if resolved_here:
            if found.outcome == "succeeded":
                await succeeded(fs.db, kind, lease.owner_id, operation_of(now))
            await _event(fs, kind.name, found.outcome)
    if now.state == "succeeded":  # found here, or resolved meanwhile by the user's Check again
        return Done(operation_id, str(now.external_id), reused=not resolved_here)
    if now.state == "failed":
        raise JobFailure(NO_EFFECT_CATEGORY)
    raise JobFailure("uncertain_write")


async def perform(
    run: JobRun,
    request: OperationRequest,
    call: Callable[[], Awaitable[CallResult]],
    *,
    keyring: KeyRing | None = None,
    kinds: OperationKinds = OPERATION_KINDS,
) -> Done:
    """One external write for ``run`` (the module docstring). Raises ``JobFailure`` (its
    category says why), ``RunStopped`` at the checkpoints and ``LeaseLost`` from the fence."""

    async def call_for(_: UUID) -> CallResult:
        return await call()

    return await perform_for(run, request, call_for, keyring=keyring, kinds=kinds)


async def perform_for(
    run: JobRun,
    request: OperationRequest,
    call: Callable[[UUID], Awaitable[CallResult]],
    *,
    keyring: KeyRing | None = None,
    kinds: OperationKinds = OPERATION_KINDS,
) -> Done:
    """``perform`` whose call gets the id of the row it writes for (the row's ``ref``, which a
    Spotify create puts into the playlist's description, ADR 0008 section 2)."""
    kind = kinds.require(request.kind)
    async with run.external_call(request.lease_seconds):
        decision = await _decide(run, request, kind)
        if isinstance(decision, Done):
            return decision
        if isinstance(decision, _Call):
            outcome = await _invoke(call, decision.operation_id, kind.name, request)
            done = await _record(run, kind, decision.operation_id, outcome)
            if done is not None:
                return done
            if outcome.state == "failed":
                assert outcome.failure is not None  # noqa: S101 - set for failed
                raise outcome.failure
            if outcome.failure is not None and outcome.failure.category == "limit_exceeded":
                # The job's request budget is used up: its reconciliation could send nothing
                # either. The row stays ``uncertain`` (the next pick supersedes a pool row; the
                # user decides a class row) and the job ends with the cap's own category.
                raise outcome.failure
    return await _reconcile_once(run, kind, decision.operation_id, keyring)
