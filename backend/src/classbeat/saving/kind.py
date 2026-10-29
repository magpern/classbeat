"""The ``save_workout`` job kind (ADR 0007 sections 5 and 6, issue SV1): Save, a new private
workout in the user's MOWL account from one approved revision.

Input ``{class_id, revision_id, content_hash, mowl_connection_id}`` (``SaveWorkoutInput``): ids
and a hash only, never a body, a program or playlist id or a name. ``consumes_credit = False``:
no ledger call, no model call, no ``agent_runs``.

**``admit``** (the jobs API's create transaction, under the owner lock): the approval checks of
``classbeat.saving.approval``, then the connection (the context's MOWL connection and
``connected``: ``not_connected``), the music link (``music_unlinked``) and the unresolved-write
rule (``write_unresolved``). A refusal is a 409 with its reason and creates nothing.

Stages, and **every attempt starts at ``prepare``** (a retry after a reconciliation that proved
no effect, after a lost lease or a shutdown, never resumes at ``save``):

1. ``prepare``: the MOWL connection is opened (it must be the input's and the context's), then one
   fenced transaction: the orphaned class writes are marked and the unresolved-write rule applied
   (another job's ``pending`` or any ``uncertain`` save/update row of the class: the job fails
   ``uncertain_write``, nothing is sent), ``hold`` share-locks the connection, the approval
   checks run again from the database (``invalid_input``, ``music_unlinked``) and the playlist id
   is read from the link row. Then the **preflight**: My Workouts' first page through
   ``call_opened``, outside any operation. Its ProgramIDs are the **baseline**. A preflight that
   fails is an ordinary failure with the usual retry classes and no operation row.
2. ``save``: ``perform`` of ``mowl_save_workout`` (``classbeat.saving.operation``), the call being
   ``workout.save(spec, creds, playlist_id)`` in a thread inside ``call_deadline_until`` of an
   instant taken before ``pending`` is written (``bound`` from then, so a late thread start
   consumes the bound instead of extending it),
   ``single_request = False`` (every failure of the write is ``uncertain``); the guard re-reads the
   link row under ``hold`` and compares the playlist id. An answer without a whole-number
   ``program_id`` is ``UncertainAnswer``.
3. ``verify`` (best effort): the new program is read back and compared with the spec
   (``classbeat.mowl_state``); equal, the saved row's ``state_hash`` is set; otherwise it stays
   NULL. The job ends ``completed`` either way: the write is final.

Everything read about the user comes from the lease's context and the stored input (read after
the fence); everything written goes through ``JobRun.fenced`` and ``perform``. Only categories
are logged.
"""

import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Final, Self, cast
from uuid import UUID

from mowl import api as mowl_api
from mowl import workout
from pydantic import BaseModel, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections, egress, mowl_state
from classbeat.connections import Credentials, OpenConnection, call_opened, hold, open_connection
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations import CallResult, OperationRequest, UncertainAnswer, orphans, perform
from classbeat.saving import approval, operation

logger = logging.getLogger(__name__)

KIND: Final = "save_workout"
STAGES: Final = ("prepare", "save", "verify")
MAX_PROGRAM_ID: Final = 2**63 - 1
# The job's failure category for each refusal of the approval in ``prepare``.
PREPARE_CATEGORY: Final = {
    "invalid_input": "invalid_input",
    "hash_mismatch": "invalid_input",
    "music_unlinked": "music_unlinked",
}

ContentHash = Annotated[str, StringConstraints(pattern=approval.HEX_SHA256)]


class SaveWorkoutInput(JobInput):
    """A Save: the class, the revision and its content hash (lower-case hex), and the MOWL
    connection the page shows. Always an existing class, never a title."""

    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    revision_id: UUID
    content_hash: ContentHash
    mowl_connection_id: UUID

    @model_validator(mode="after")
    def _names_its_class(self) -> Self:
        if self.class_id is None:
            raise ValueError("a save names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id

    @property
    def digest(self) -> bytes:
        return bytes.fromhex(self.content_hash)


@dataclass(frozen=True)
class Runtime:
    """What the worker gives the kind: the key ring (to open the MOWL connection)."""

    keyring: KeyRing


_runtime: Runtime | None = None


def configure(runtime: Runtime | None) -> None:
    """Set (or clear, with None) the worker's runtime (Save's and Update's)."""
    global _runtime
    _runtime = runtime


def current_runtime() -> Runtime | None:
    return _runtime


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    """Section 5.1, inside the create transaction; nothing is stored on a refusal."""
    if not isinstance(parsed, SaveWorkoutInput):
        return Refused("invalid_input")
    found = await approval.revision(db, ctx, parsed.the_class, parsed.revision_id, parsed.digest)
    if isinstance(found, approval.Refusal):
        return Refused(found.reason)
    if ctx.connection("mowl") != parsed.mowl_connection_id:
        return Refused("not_connected")
    info = await connections.find_in(db, ctx, parsed.mowl_connection_id)
    if info is None or info.provider != "mowl" or info.status != "connected":
        return Refused("not_connected")
    if await approval.linked_playlist(db, ctx, found.link_id) is None:
        return Refused("music_unlinked")
    if await orphans.write_unresolved(db, ctx, parsed.the_class):
        return Refused("write_unresolved")
    return Admitted()


@dataclass(frozen=True)
class Plan:
    """What ``prepare`` approved: the revision, the playlist id under ``hold``, the opened
    connection and the baseline."""

    revision: approval.Revision
    playlist_id: int
    opened: OpenConnection
    baseline: list[int]


JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def _approved(run: JobRun, given: SaveWorkoutInput, opened: OpenConnection) -> Plan:
    lease = run.lease
    async with run.fenced() as fs:
        # Its own transaction, so the marking stays when the save is refused below.
        await orphans.resolve(fs.db, run.ctx)
    async with run.fenced() as fs:
        job_class = (await fs.db.execute(JOB_CLASS, lease.params())).scalar_one_or_none()
        if job_class != given.the_class:
            raise JobFailure("internal")  # the API stores the input's class: a bug, never a save
        if await orphans.write_unresolved(fs.db, run.ctx, given.the_class, lease.job_id):
            raise JobFailure("uncertain_write")
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        found = await approval.revision(
            fs.db, run.ctx, given.the_class, given.revision_id, given.digest
        )
        if isinstance(found, approval.Refusal):
            raise JobFailure(PREPARE_CATEGORY.get(found.reason, "invalid_input"))
        playlist_id = await approval.linked_playlist(fs.db, run.ctx, found.link_id)
        if playlist_id is None:
            raise JobFailure("music_unlinked")
    return Plan(found, playlist_id, opened, [])


async def preflight(run: JobRun, opened: OpenConnection) -> list[int]:
    """My Workouts' first page, outside any operation: the baseline (Save and Update)."""
    await run.checkpoint()
    listing = await call_opened(
        run.factory, run.ctx, opened, lambda c: mowl_api.list_workouts(c, page=0)
    )
    ids = operation.listed_ids(listing)
    if ids is None or len(ids) > operation.PAGE_SIZE:
        raise JobFailure("upstream_unavailable")  # nothing written; retried like an outage
    return ids


async def prepare(run: JobRun, keyring: KeyRing, given: SaveWorkoutInput) -> Plan:
    if run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != given.mowl_connection_id:
        raise JobFailure("not_connected")
    plan = await _approved(run, given, opened)
    baseline = await preflight(run, opened)
    return Plan(plan.revision, plan.playlist_id, opened, baseline)


_LINK_STILL = text(
    "SELECT playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND id = :link_id AND source = 'spotify'"
)


def program_id_of_answer(answer: object) -> int:
    value: Any = None
    if isinstance(answer, dict):
        value = cast(dict[str, Any], answer).get("program_id")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_PROGRAM_ID:
        raise UncertainAnswer
    return value


def write_program(
    spec: dict[str, Any],
    creds: Credentials,
    playlist_id: int,
    deadline_at: float,
    program_id: int | None = None,
) -> Any:
    """The library's save in the call's thread: every request it makes ends by ``deadline_at``
    (a ``time.monotonic()`` instant taken before the ``pending`` row was written, so a thread
    that starts late uses up the bound instead of extending it). With ``program_id`` (Update,
    ``classbeat.saving.update``) the library re-saves that program in place."""
    with egress.call_deadline_until(deadline_at):
        if program_id is None:
            return workout.save(spec, creds, playlist_id)
        return workout.save(spec, creds, playlist_id, program_id=program_id)


async def save(run: JobRun, keyring: KeyRing, plan: Plan) -> int:
    """Stage ``save``: the write as an external operation; returns the ProgramID."""
    revision, opened, playlist_id = plan.revision, plan.opened, plan.playlist_id
    bound = operation.bound_seconds(revision.segments)
    # Anchored here, before ``perform`` writes ``pending`` (``pending_at = now()``): the call's
    # requests end by ``pending_at + bound`` at the latest, however late its thread starts, so
    # ``actionable_at`` (``pending_at + bound + 30 s``) never comes while one may still be sent.
    deadline_at = time.monotonic() + bound

    async def guard(db: AsyncSession) -> bool:
        if not await hold(db, run.ctx, opened):
            return False
        row = (
            await db.execute(
                _LINK_STILL, {"owner_id": run.lease.owner_id, "link_id": revision.link_id}
            )
        ).first()
        return row is not None and row.playlist_id == playlist_id

    async def call() -> CallResult:
        answer = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda creds: write_program(revision.spec, creds, playlist_id, deadline_at),
        )
        return CallResult(str(program_id_of_answer(answer)), {"segments": revision.segments})

    request = OperationRequest(
        kind=operation.KIND,
        idempotency_key=operation.idempotency_key(run.lease.job_id),
        request_hash=operation.save_hash(revision.content_hash_hex, playlist_id),
        timeout_seconds=operation.REQUEST_SECONDS,
        guard=guard,
        single_request=False,  # many requests: none of its failures proves no effect
        class_id=revision.class_id,
        context=operation.save_context(
            revision.revision_id, revision.content_hash_hex, playlist_id, plan.baseline
        ),
        bound_seconds=bound,
    )
    done = await perform(run, request, call, keyring=keyring)
    return int(done.external_id)


_STATE = text(
    "UPDATE saved_workouts SET state_hash = :state_hash, updated_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND program_id = :program_id "
    "AND detached_at IS NULL AND state_hash IS NULL"
)


async def verify(run: JobRun, plan: Plan, program_id: int) -> bool:
    """Stage ``verify``: best effort. True when the read-back matched and was recorded."""
    return await verify_program(
        run, plan.opened, plan.revision.spec, plan.playlist_id, plan.revision.class_id, program_id
    )


async def verify_program(
    run: JobRun,
    opened: OpenConnection,
    spec: dict[str, Any],
    playlist_id: int,
    class_id: UUID,
    program_id: int,
) -> bool:
    """Read the written program back and, when it holds exactly ``spec`` with ``playlist_id``,
    record its normalized state on the live saved row of ``class_id`` whose state is not yet
    recorded (Save's new row; Update's row, whose state ``on_succeeded`` cleared). Best effort:
    any failure leaves the row "saved, not checked"."""
    try:
        program = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda c: mowl_api.get_workout(c, program_id),
        )
        if mowl_state.program_id_of(program) != program_id:
            return False
        if not mowl_state.matches(spec, program, playlist_id=playlist_id):
            return False
        digest = mowl_state.state_hash(mowl_state.normalized_program(program))
    except Exception as exc:  # a failed read leaves the row "saved, not checked"
        logger.info("save not verified", extra={"error_type": type(exc).__name__})
        return False
    async with run.fenced() as fs:
        await fs.db.execute(
            _STATE,
            {
                "state_hash": digest,
                "owner_id": run.lease.owner_id,
                "class_id": class_id,
                "program_id": program_id,
            },
        )
    return True


async def run(job_run: JobRun) -> Complete:
    runtime = _runtime
    if runtime is None:
        raise JobFailure("internal")
    given = job_run.input
    if not isinstance(given, SaveWorkoutInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("prepare")
    plan = await prepare(job_run, runtime.keyring, given)
    await job_run.stage("save")
    program_id = await save(job_run, runtime.keyring, plan)
    await job_run.stage("verify")
    await verify(job_run, plan, program_id)
    return Complete()


SAVE_WORKOUT: Final = KINDS.register(
    JobKind(KIND, SaveWorkoutInput, STAGES, run, consumes_credit=False, admit=admit)
)
