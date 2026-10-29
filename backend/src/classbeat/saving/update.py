"""The ``update_workout`` job kind (ADR 0007 sections 5, 7 and 8, issue SV2): **Update saved**,
an existing program of the user's MOWL account re-saved in place from one approved revision.

Input ``{class_id, revision_id, content_hash, saved_workout_id, overwrite, mowl_connection_id}``
(``UpdateWorkoutInput``): ids, a hash and the user's confirmation, never a body, a program or
playlist id or a name (``extra="forbid"``: an input carrying a program id is refused). **The
program id is read only from the user's own live ``saved_workouts`` row** of the class, inside
the transaction that checks it (``approval.saved_target``): never from the browser, a model or
MOWL's listing. ``overwrite`` is the page's "Update anyway" confirmation; it skips only the
read-first comparison. ``consumes_credit = False``: no ledger call, no model call.

**``admit``** (the jobs API's create transaction, under the owner lock): the revision checks of
``classbeat.saving.approval``, the connection (``not_connected``), the saved row
(``invalid_input``: another user's, another class's, a detached or an unknown row), the music
link (``music_unlinked``) and the unresolved-write rule (``write_unresolved``). A refusal is a
409 with its reason and creates nothing.

Stages, and **every attempt starts at ``prepare``** (a retry after a reconciliation that proved
no effect never resumes at ``save``, so the read-first check runs again):

1. ``prepare``: the connection is opened (the input's and the context's), then one fenced
   transaction: orphaned class writes marked, the unresolved-write rule (``uncertain_write``),
   ``hold``, the revision, the saved row (its program id) and the link (its playlist id) again
   from the database. Then the **preflight**: My Workouts' first page, outside any operation;
   its ProgramIDs are the **baseline**. A failing preflight is an ordinary failure, no row.
2. ``read`` (outside any operation): ``get_workout(program_id)``. Not there, or not served to
   this account: ``program_missing``. Public, not normalizable, or its normalized state not the
   saved row's ``state_hash`` (or that is NULL): ``program_changed``, unless ``overwrite``.
   Nothing is written in either case. Its normalized hash is the write's ``before_hash``.
3. ``save``: ``perform`` of ``mowl_update_workout`` (``classbeat.saving.update_operation``), the
   call being ``workout.save(spec, creds, playlist_id, program_id=program_id)`` inside
   ``call_deadline_until`` of an instant taken before ``pending`` is written,
   ``single_request = False``; the guard re-reads the link row and the saved row under ``hold``
   and compares the playlist id and the program id. An answer naming another program is
   ``UncertainAnswer`` (the library raises first: MOWL made a copy).
4. ``verify`` (best effort): the program is read back; equal to the spec, the saved row's
   ``state_hash`` is set, otherwise it stays NULL. The job ends ``completed`` either way.
"""

import time
from dataclasses import dataclass
from typing import ClassVar, Final, Self
from uuid import UUID

from mowl import api as mowl_api
from pydantic import BaseModel, StrictBool, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections, mowl_state
from classbeat.connections import (
    ConnectionFailure,
    OpenConnection,
    call_opened,
    hold,
    open_connection,
)
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations import CallResult, OperationRequest, UncertainAnswer, orphans, perform
from classbeat.saving import approval, operation, update_operation
from classbeat.saving.kind import (
    JOB_CLASS,
    PREPARE_CATEGORY,
    ContentHash,
    current_runtime,
    preflight,
    program_id_of_answer,
    verify_program,
    write_program,
)

KIND: Final = "update_workout"
STAGES: Final = ("prepare", "read", "save", "verify")
# A read the library answers with anything but a transport, rate or token failure: the program
# is not there for this account (deleted, or not served to it). ``unknown`` is MOWL's own error
# answer (HTTP 200 with ``Error``) or a 4xx; nothing is written either way.
MISSING_CATEGORIES: Final = frozenset({"unknown"})


class UpdateWorkoutInput(JobInput):
    """An Update: the class, the revision and its content hash (lower-case hex), the user's
    saved row, the "Update anyway" confirmation and the MOWL connection the page shows."""

    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    revision_id: UUID
    content_hash: ContentHash
    saved_workout_id: UUID
    overwrite: StrictBool = False
    mowl_connection_id: UUID

    @model_validator(mode="after")
    def _names_its_class(self) -> Self:
        if self.class_id is None:
            raise ValueError("an update names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id

    @property
    def digest(self) -> bytes:
        return bytes.fromhex(self.content_hash)


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    """Section 5.1, inside the create transaction; nothing is stored on a refusal."""
    if not isinstance(parsed, UpdateWorkoutInput):
        return Refused("invalid_input")
    found = await approval.revision(db, ctx, parsed.the_class, parsed.revision_id, parsed.digest)
    if isinstance(found, approval.Refusal):
        return Refused(found.reason)
    if ctx.connection("mowl") != parsed.mowl_connection_id:
        return Refused("not_connected")
    info = await connections.find_in(db, ctx, parsed.mowl_connection_id)
    if info is None or info.provider != "mowl" or info.status != "connected":
        return Refused("not_connected")
    if await approval.saved_target(db, ctx, parsed.the_class, parsed.saved_workout_id) is None:
        return Refused("invalid_input")
    if await approval.linked_playlist(db, ctx, found.link_id) is None:
        return Refused("music_unlinked")
    if await orphans.write_unresolved(db, ctx, parsed.the_class):
        return Refused("write_unresolved")
    return Admitted()


@dataclass(frozen=True)
class Plan:
    """What ``prepare`` approved: the revision, the playlist id and the target program, both
    read under ``hold``, the opened connection and the baseline."""

    revision: approval.Revision
    playlist_id: int
    target: approval.SavedTarget
    opened: OpenConnection
    baseline: list[int]


async def _approved(
    run: JobRun, given: UpdateWorkoutInput, opened: OpenConnection
) -> tuple[approval.Revision, int, approval.SavedTarget]:
    lease = run.lease
    async with run.fenced() as fs:
        # Its own transaction, so the marking stays when the update is refused below.
        await orphans.resolve(fs.db, run.ctx)
    async with run.fenced() as fs:
        job_class = (await fs.db.execute(JOB_CLASS, lease.params())).scalar_one_or_none()
        if job_class != given.the_class:
            raise JobFailure("internal")  # the API stores the input's class: a bug, never a write
        if await orphans.write_unresolved(fs.db, run.ctx, given.the_class, lease.job_id):
            raise JobFailure("uncertain_write")
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        found = await approval.revision(
            fs.db, run.ctx, given.the_class, given.revision_id, given.digest
        )
        if isinstance(found, approval.Refusal):
            raise JobFailure(PREPARE_CATEGORY.get(found.reason, "invalid_input"))
        target = await approval.saved_target(
            fs.db, run.ctx, given.the_class, given.saved_workout_id
        )
        if target is None:
            raise JobFailure("invalid_input")  # detached (a reconnect), gone, or not this class's
        playlist_id = await approval.linked_playlist(fs.db, run.ctx, found.link_id)
        if playlist_id is None:
            raise JobFailure("music_unlinked")
    return found, playlist_id, target


async def prepare(run: JobRun, keyring: KeyRing, given: UpdateWorkoutInput) -> Plan:
    if run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != given.mowl_connection_id:
        raise JobFailure("not_connected")
    revision, playlist_id, target = await _approved(run, given, opened)
    baseline = await preflight(run, opened)
    return Plan(revision, playlist_id, target, opened, baseline)


def _check_read(program: object, plan: Plan, overwrite: bool) -> str | None:
    """The read-first rule on a read program; returns ``before_hash`` (hex, or None when the
    program can't be normalized and ``overwrite`` lets it through)."""
    if mowl_state.program_id_of(program) != plan.target.program_id:
        raise JobFailure("program_missing")  # an empty answer, or another program
    try:
        normal = mowl_state.normalized_program(program)
    except ValueError:
        normal = None
    before = None if normal is None else mowl_state.state_hash(normal).hex()
    if overwrite:
        return before
    if normal is None or normal["public"] is not False:
        raise JobFailure("program_changed")  # the library writes it private: changed in MOWL
    recorded = plan.target.state_hash
    if recorded is None or before is None or bytes.fromhex(before) != recorded:
        raise JobFailure("program_changed")
    return before


async def read_first(run: JobRun, plan: Plan, overwrite: bool) -> str | None:
    """Stage ``read``: ``get_workout`` of the saved program, outside any operation."""
    await run.checkpoint()
    program_id = plan.target.program_id
    failure: ConnectionFailure | None = None
    program: object = None
    try:
        program = await call_opened(
            run.factory, run.ctx, plan.opened, lambda c: mowl_api.get_workout(c, program_id)
        )
    except ConnectionFailure as error:
        failure = error
    if failure is not None:
        if failure.category in MISSING_CATEGORIES:
            raise JobFailure("program_missing")
        raise failure  # an outage, a rate limit, a token MOWL refused: the usual retry classes
    return _check_read(program, plan, overwrite)


async def save(run: JobRun, keyring: KeyRing, plan: Plan, before_hash: str | None) -> int:
    """Stage ``save``: the update as an external operation; returns the ProgramID."""
    revision, opened, playlist_id = plan.revision, plan.opened, plan.playlist_id
    target = plan.target
    bound = operation.bound_seconds(revision.segments)
    # Anchored before ``perform`` writes ``pending``, as for Save (``classbeat.saving.kind``).
    deadline_at = time.monotonic() + bound

    async def guard(db: AsyncSession) -> bool:
        if not await hold(db, run.ctx, opened):
            return False
        playlist_now = await approval.linked_playlist(db, run.ctx, revision.link_id)
        target_now = await approval.saved_target(
            db, run.ctx, revision.class_id, target.saved_workout_id
        )
        return (
            playlist_now == playlist_id
            and target_now is not None
            and target_now.program_id == target.program_id
        )

    async def call() -> CallResult:
        answer = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda creds: write_program(
                revision.spec, creds, playlist_id, deadline_at, target.program_id
            ),
        )
        if program_id_of_answer(answer) != target.program_id:
            raise UncertainAnswer  # another program: MOWL made a copy
        return CallResult(str(target.program_id), {"segments": revision.segments})

    request = OperationRequest(
        kind=update_operation.KIND,
        idempotency_key=update_operation.idempotency_key(run.lease.job_id),
        request_hash=update_operation.update_hash(
            target.program_id, revision.content_hash_hex, playlist_id
        ),
        timeout_seconds=operation.REQUEST_SECONDS,
        guard=guard,
        single_request=False,  # many requests: none of its failures proves no effect
        class_id=revision.class_id,
        context=update_operation.update_context(
            target.saved_workout_id,
            revision.revision_id,
            revision.content_hash_hex,
            playlist_id,
            target.program_id,
            before_hash,
            plan.baseline,
        ),
        bound_seconds=bound,
    )
    done = await perform(run, request, call, keyring=keyring)
    if done.external_id != str(target.program_id):
        raise JobFailure("internal")  # the key and hash name this program only
    return target.program_id


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    if runtime is None:
        raise JobFailure("internal")
    given = job_run.input
    if not isinstance(given, UpdateWorkoutInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("prepare")
    plan = await prepare(job_run, runtime.keyring, given)
    await job_run.stage("read")
    before_hash = await read_first(job_run, plan, given.overwrite)
    await job_run.stage("save")
    program_id = await save(job_run, runtime.keyring, plan, before_hash)
    await job_run.stage("verify")
    await verify_program(
        job_run,
        plan.opened,
        plan.revision.spec,
        plan.playlist_id,
        plan.revision.class_id,
        program_id,
    )
    return Complete()


UPDATE_WORKOUT: Final = KINDS.register(
    JobKind(KIND, UpdateWorkoutInput, STAGES, run, consumes_credit=False, admit=admit)
)
