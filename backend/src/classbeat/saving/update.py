"""The ``update_workout`` job kind (ADR 0007 sections 5, 7 and 8, issue SV2): **Update saved**,
an existing program of the user's MOWL account re-saved in place from one approved revision.

Input ``{class_id, revision_id, content_hash, saved_workout_id, overwrite, mowl_connection_id}``
(``UpdateWorkoutInput``): ids, a hash and the user's confirmation, never a body, a program or
playlist id or a name (``extra="forbid"``: an input carrying a program id is refused). **The
program id is read only from the user's own live ``saved_workouts`` row** of the class, inside
the transaction that checks it (``targets.saved_target``): never from the browser, a model or
MOWL's listing. ``overwrite`` is the page's "Update anyway" confirmation; it skips only the
read-first comparison, never the sport check. ``consumes_credit = False``: no ledger call, no
model call.

**``admit``** (the jobs API's create transaction, under the owner lock): first Save's and Update's
shared caps (#577, ``classbeat.saving.caps``), then the revision checks of
``classbeat.saving.approval``, ``running_enabled`` for a running class and the soundless switch for
a soundless revision (``feature_off``), the connection (``not_connected``), the saved row
(``invalid_input``: another user's, another class's, a detached or an unknown row), the music
link (``music_unlinked``; a soundless revision names none and writes ``PlaylistID: -1``) and
the unresolved-write rule (``write_unresolved``), last the user's current plan (409 ``quota``,
as Save: the program already in MOWL is never touched by a refusal).
A refusal is a 409 with its reason and creates nothing.

Stages, and **every attempt starts at ``prepare``** (a retry after a reconciliation that proved
no effect never resumes at ``save``, so the read-first check runs again):

1. ``prepare``: the connection is opened (the input's and the context's), then one fenced
   transaction: orphaned class writes marked, the unresolved-write rule (``uncertain_write``),
   ``hold``, the revision, ``running_enabled`` for a running class (``feature_off``, ADR 0018
   section 5) and the soundless switch for a soundless revision (``feature_off``), the saved row
   (its program id) and the link (its playlist id; ``-1`` and no link read for a soundless
   revision) again from the database. Then the **preflight**: My Workouts' first page, outside
   any operation; its ProgramIDs are the **baseline**. A failing preflight is an ordinary
   failure, no row.
2. ``read`` (outside any operation): ``get_workout(program_id)``. Not there, or not served to
   this account: ``program_missing``. Its own ``ActivityTypeID`` missing, unknown or not the
   class's sport (``mowl_state.program_sport`` against ``approval.Revision.sport``, the class's
   stored brief's, ADR 0018 M8 R1b): ``program_other_sport``, **also with ``overwrite``**, in
   both directions (a running class onto a cycling program and the reverse).
   Public, not normalizable, or its normalized state not the saved row's ``state_hash`` (or
   that is NULL): ``program_changed``, unless ``overwrite``. Nothing is written in any of these
   cases. Its normalized hash is the write's ``before_hash``. One
   exception keeps a crashed attempt's write from being reported as the user's edit: when this
   job's own operation row (key ``<job_id>:update``) is ``pending``, ``uncertain`` or
   ``succeeded``, the refusal is deferred to the decide guard, ``perform`` meets the row and
   never calls (it reconciles, waits for the user or reuses the success), and the guard refuses
   with the deferred category if the row became ``failed`` meanwhile.
3. ``save``: ``perform`` of ``mowl_update_workout`` (``classbeat.saving.update_operation``), the
   call being ``workout.save(spec, creds, playlist_id, program_id=program_id)`` inside
   ``call_deadline_until`` of an instant taken before ``pending`` is written,
   ``single_request = False``. For a running class the library reads the target once more
   inside the call and refuses, before any write, anything but that program with its own
   ``ActivityTypeID`` 1 (mowl-workouts 0.9.0); the bound counts that read. The guard re-reads
   the link row (none for a soundless revision) and the saved row under ``hold`` and compares
   the playlist id and the program id. An answer naming another program is ``UncertainAnswer``
   (the library raises first: MOWL made a copy). **Unverified live:** that MOWL clears a
   playlist attached in the app when a cycling update sends ``PlaylistID: -1`` (ADR 0007
   amendment of 2026-11-03, item 8, as amended 2026-11-04). If it keeps it, a received answer
   ends ``completed`` but never verified: the read after the write does not match (``verify``
   leaves ``state_hash`` NULL, so the next Update reads the program as changed); a lost answer
   stays ``uncertain`` (the reconciler is inconclusive). The panel's sentence for the unverified
   case is SL6's (#532).
4. ``verify`` (best effort): the program is read back; equal to the spec, the saved row's
   ``state_hash`` is set, otherwise it stays NULL. The job ends ``completed`` either way.
"""

import time
from dataclasses import dataclass
from typing import ClassVar, Final, Self
from uuid import UUID

from mowl import api as mowl_api
from pydantic import BaseModel, StrictBool, model_validator
from sqlalchemy import text
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
from classbeat.quota.gates import check_stored_draft
from classbeat.saving import approval, operation, targets, update_operation
from classbeat.saving.caps import check_write_caps
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
    # #577: first, so a script at the hour's or day's cap learns nothing else (409 ``quota``).
    await check_write_caps(db, ctx)
    found = await approval.revision(db, ctx, parsed.the_class, parsed.revision_id, parsed.digest)
    if isinstance(found, approval.Refusal):
        return Refused(found.reason)
    if await approval.running_refused(db, found):
        return Refused("feature_off")  # ADR 0018 section 5: running is off now
    if approval.soundless_refused(found):
        return Refused("feature_off")  # soundless is switched off now
    if ctx.connection("mowl") != parsed.mowl_connection_id:
        return Refused("not_connected")
    info = await connections.find_in(db, ctx, parsed.mowl_connection_id)
    if info is None or info.provider != "mowl" or info.status != "connected":
        return Refused("not_connected")
    if await targets.saved_target(db, ctx, parsed.the_class, parsed.saved_workout_id) is None:
        return Refused("invalid_input")
    if await approval.playlist_for(db, ctx, found) is None:
        return Refused("music_unlinked")
    if await orphans.write_unresolved(db, ctx, parsed.the_class):
        return Refused("write_unresolved")
    # #496: no model runs, so the revision must fit the user's current plan (409 ``quota``).
    await check_stored_draft(db, ctx, found.spec)
    return Admitted()


@dataclass(frozen=True)
class Plan:
    """What ``prepare`` approved: the revision, the playlist id and the target program, both
    read under ``hold``, the opened connection and the baseline."""

    revision: approval.Revision
    playlist_id: int
    target: targets.SavedTarget
    opened: OpenConnection
    baseline: list[int]


async def _approved(
    run: JobRun, given: UpdateWorkoutInput, opened: OpenConnection
) -> tuple[approval.Revision, int, targets.SavedTarget]:
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
        if await approval.running_refused(fs.db, found):
            raise JobFailure("feature_off")  # switched off since the job was queued
        if approval.soundless_refused(found):
            raise JobFailure("feature_off")  # the worker's soundless switch is off
        target = await targets.saved_target(fs.db, run.ctx, given.the_class, given.saved_workout_id)
        if target is None:
            raise JobFailure("invalid_input")  # detached (a reconnect), gone, or not this class's
        playlist_id = await approval.playlist_for(fs.db, run.ctx, found)
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


@dataclass(frozen=True)
class Read:
    """What the read found: ``before_hash`` (hex; None when the program can't be normalized)
    and ``refusal`` (``program_changed``, ``program_missing`` or ``program_other_sport``) when
    the read-first rule refused but this job's own earlier attempt left its operation row
    undecided or ``succeeded``. The write then goes ahead only into ``perform``, which never
    calls for such a row, and the guard refuses with ``refusal`` if the row became ``failed``
    (or went) meanwhile."""

    before_hash: str | None
    refusal: str | None = None


def _refusal(program: object, plan: Plan, overwrite: bool) -> tuple[str | None, str | None]:
    """The read-first rule on a read program: ``(before_hash, refusal)``. The sport check comes
    before ``overwrite``: "Update anyway" replaces the user's changes, never the sport."""
    if mowl_state.program_id_of(program) != plan.target.program_id:
        return None, "program_missing"  # an empty answer, or another program
    try:
        normal = mowl_state.normalized_program(program)
    except ValueError:
        normal = None
    before = None if normal is None else mowl_state.state_hash(normal).hex()
    sport = mowl_state.program_sport(program)
    if sport is None or sport != plan.revision.sport:
        # Another sport, or one we cannot tell (missing or unknown ``ActivityTypeID``): the
        # library would rewrite it with this class's sport and bodies (issue #485).
        return before, "program_other_sport"
    if overwrite:
        return before, None
    if normal is None or normal["public"] is not False:
        return before, "program_changed"  # the library writes it private: changed in MOWL
    recorded = plan.target.state_hash
    if recorded is None or before is None or bytes.fromhex(before) != recorded:
        return before, "program_changed"
    return before, None


# This job's own operation row (its key is per job): an earlier attempt's write.
_OWN_ROW = text(
    "SELECT state FROM external_operations "
    "WHERE owner_id = :owner_id AND kind = :kind AND idempotency_key = :key"
)
_OWN_ROW_LOCKED = text(
    "SELECT state FROM external_operations "
    "WHERE owner_id = :owner_id AND kind = :kind AND idempotency_key = :key FOR UPDATE"
)
# An own row in these states is never sent again by ``perform`` (it is reused, reconciled or
# left to the user): only a ``failed`` row (proved no effect) or no row leads to a call.
NO_CALL_STATES: Final = frozenset({"pending", "uncertain", "succeeded"})


def _own_key(run: JobRun) -> dict[str, object]:
    return {
        "owner_id": run.lease.owner_id,
        "kind": update_operation.KIND,
        "key": update_operation.idempotency_key(run.lease.job_id),
    }


async def read_first(run: JobRun, plan: Plan, overwrite: bool) -> Read:
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
    if failure is not None and failure.category not in MISSING_CATEGORIES:
        raise failure  # an outage, a rate limit, a token MOWL refused: the usual retry classes
    before, refusal = (None, "program_missing") if failure else _refusal(program, plan, overwrite)
    if refusal is None:
        return Read(before)
    # A crash or lost lease mid-call left this job's own row: the program may differ because of
    # that very write. ``perform`` decides it without a call (ADR 0007 section 8).
    async with run.fenced() as fs:
        state = (await fs.db.execute(_OWN_ROW, _own_key(run))).scalar_one_or_none()
    if state not in NO_CALL_STATES:
        raise JobFailure(refusal)  # nothing written
    return Read(before, refusal)


async def save(run: JobRun, keyring: KeyRing, plan: Plan, read: Read) -> int:
    """Stage ``save``: the update as an external operation; returns the ProgramID."""
    revision, opened, playlist_id = plan.revision, plan.opened, plan.playlist_id
    target = plan.target
    # A running update's call starts with the library's own read of the target (0.9.0).
    bound = operation.bound_seconds(revision.segments, reads=1 if revision.running else 0)
    # Anchored before ``perform`` writes ``pending``, as for Save (``classbeat.saving.kind``).
    deadline_at = time.monotonic() + bound

    async def guard(db: AsyncSession) -> bool:
        if not await hold(db, run.ctx, opened):
            return False
        # A soundless revision names no link: ``NO_PLAYLIST`` without a read (``playlist_for``).
        playlist_now = await approval.playlist_for(db, run.ctx, revision)
        target_now = await targets.saved_target(
            db, run.ctx, revision.class_id, target.saved_workout_id
        )
        if not (
            playlist_now == playlist_id
            and target_now is not None
            and target_now.program_id == target.program_id
        ):
            return False
        if read.refusal is not None:
            # Locked here, before ``perform`` locks it again: Do it anyway can't turn it
            # ``failed`` (which ``perform`` would take over and call) after this check.
            state = (await db.execute(_OWN_ROW_LOCKED, _own_key(run))).scalar_one_or_none()
            if state not in NO_CALL_STATES:
                raise JobFailure(read.refusal)
        return True

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
            read.before_hash,
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
    read = await read_first(job_run, plan, given.overwrite)
    await job_run.stage("save")
    program_id = await save(job_run, runtime.keyring, plan, read)
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
