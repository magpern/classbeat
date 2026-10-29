"""The ``revise_draft`` job kind (ADR 0007 section 3, M3b R1): a class revised by chat.

Input: ``{class_id, base_revision_id, request, mowl_connection_id}`` (``ReviseDraftInput``), plus
two fields only ``admit`` sets: ``base_revision_hash`` and ``request_message_id``. It consumes no
credit (a class is one credit however often it is revised) and writes nothing outside classbeat.

**Create time** (``admit``, in the create transaction under the owner lock, the class row locked):
``invalid_input`` unless the base is a ``draft`` of the class; ``stale_base`` unless it is the
class's current revision; ``revision_limit`` and ``chat_full`` (``classbeat.design.limits``).
It stores the base's hash and inserts the user's ``class_messages`` row with the job.

**Pipeline** (stages ``music``, ``revise``), every attempt from the top with a new provider, so a
retry restarts the stage from its beginning and the final store is the only write:

1. the class's brief, its ``music_source`` and the base (``classbeat.design.chat``, one fenced
   read; the base's hash is checked against the one ``admit`` stored and against its content);
2. the music step of ``design_draft`` (``music.load_music``); a fingerprint that differs from the
   base revision's is told to the model as a fact;
3. the ``revise`` stage (``classbeat.agent.revise``) over the brief, the music summary, the base
   draft and the user's requests along the ``parent_id`` chain. No earlier note or model text;
4. the final fenced transaction (``Complete``): the connection guard, the revision (parent = base,
   provenance ``origin = revise_draft``; the base again is ``invalid_output``), the assistant
   message (the model's note and the backend's change summary), and the compare-and-set of
   ``current_revision_id`` (the new revision becomes current only if the base still is).
"""

import hashlib
import uuid
from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import Brief, WorkoutDraft, run_stage
from classbeat.agent.records import JobStageHost
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.agent.schemas import clean_text
from classbeat.context import UserContext
from classbeat.design import chat, diff, limits, revision
from classbeat.design.kind import MusicChoice, current_runtime, new_provider
from classbeat.design.music import load_music
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused

KIND: Final = "revise_draft"
STAGES: Final = ("music", "revise")
MAX_REQUEST: Final = 2000


def _visible(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise ValueError("must have visible text")
    return cleaned


Request = Annotated[str, Field(min_length=1, max_length=MAX_REQUEST), AfterValidator(_visible)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ReviseDraftInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    base_revision_id: UUID
    request: Request
    mowl_connection_id: UUID
    # Server fields: only ``admit`` sets them (a browser sending one is refused).
    base_revision_hash: Sha256Hex | None = None
    request_message_id: UUID | None = None

    @model_validator(mode="after")
    def _a_class_of_its_own(self) -> Self:
        if self.class_id is None:
            raise ValueError("a revision names its class")
        return self


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    if not isinstance(parsed, ReviseDraftInput) or parsed.class_id is None:
        raise ValueError("admit gets this kind's input")
    given, class_id = parsed, parsed.class_id
    state = await chat.create_state(db, ctx.user_id, class_id, given.base_revision_id)
    if not state.exists or state.base_kind != "draft" or state.base_hash is None:
        return Refused("invalid_input")  # a foreign or unknown id answers like any other
    if state.current != given.base_revision_id:
        return Refused("stale_base")
    chat_limits = limits.current()
    allowed = await chat_limits.policy.limit_for(db, ctx, class_id)
    if state.chat_revisions >= allowed:
        return Refused("revision_limit", {"limit": allowed, "count": state.chat_revisions})
    if state.requests >= chat_limits.max_requests:
        return Refused("chat_full", {"limit": chat_limits.max_requests, "count": state.requests})
    message_id = uuid.uuid4()

    async def after_insert(db: AsyncSession, job_id: UUID) -> None:
        try:
            await chat.insert_message(
                db,
                owner_id=ctx.user_id,
                class_id=class_id,
                job_id=job_id,
                role="user",
                body=given.request,
                base_revision_id=given.base_revision_id,
                message_id=message_id,
            )
        except chat.MessageRefused:
            raise RuntimeError("the job is not a job of this class") from None

    return Admitted(
        {"base_revision_hash": state.base_hash.hex(), "request_message_id": message_id},
        after_insert,
    )


_CAS = text(
    "UPDATE classes c SET current_revision_id = :revision_id, updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id = :base RETURNING c.id"
)


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    if runtime is None:
        raise JobFailure("internal")
    given = job_run.input
    if (
        not isinstance(given, ReviseDraftInput)
        or job_run.approved
        or given.class_id is None
        or given.base_revision_hash is None
        or given.request_message_id is None
    ):
        raise JobFailure("internal")
    class_id, base_id = given.class_id, given.base_revision_id
    request_message_id = given.request_message_id
    async with job_run.fenced() as fs:
        base = await chat.load_base(
            fs.db, fs.lease.owner_id, fs.lease.job_id, base_id, runtime.context_requests
        )
    if base is None or base.kind != "draft" or not base.brief or not base.music_source:
        raise JobFailure("invalid_input")
    base_hash = bytes.fromhex(given.base_revision_hash)
    if (
        base.content_hash != base_hash
        or hashlib.sha256(revision.canonical(base.content)).digest() != base_hash
    ):
        raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
    try:
        brief = Brief.model_validate(base.brief)
        music = MusicChoice.model_validate(base.music_source)
        base_draft = WorkoutDraft.model_validate(base.content)
    except ValidationError:
        raise JobFailure("internal") from None
    provider = new_provider(runtime)  # before any MOWL call; this run's own
    await job_run.stage("music")
    step = await load_music(
        job_run,
        runtime.keyring,
        music.spotify_playlist_id,
        connection_id=given.mowl_connection_id,
    )
    try:
        ctx = ReviseContext.build(
            brief,
            step.data,
            base_draft,
            base.earlier_requests,
            given.request,
            music_changed=step.fingerprint != base.fingerprint,
        )
        outcome = await run_stage(
            JobStageHost(job_run), provider, revise_stage(ctx), ctx.first_message(), runtime.agent
        )
        if not isinstance(outcome.output, ReviseResult):
            raise JobFailure("internal")
        revised = outcome.output
        draft = revision.build(
            revised.result,
            outcome,
            ctx.map,
            step,
            origin="revise_draft",
            base_revision_id=base_id,
            base_hash=base_hash,
            request_message_id=request_message_id,
        )
        change = diff.summarize(base.content, draft.content)
    except BaseException:
        step.close()
        raise

    async def store(fs: FencedSession) -> UUID:
        """The connection guard, the revision, the compare-and-set, the assistant message."""
        try:
            await step.still_current(fs)
            revision_id = await draft.store(fs)
            params = {
                "revision_id": revision_id,
                "job_id": fs.lease.job_id,
                "owner_id": fs.lease.owner_id,
                "base": base_id,
            }
            await fs.db.execute(_CAS, params)  # no row: kept, but not current
            await chat.insert_message(
                fs.db,
                owner_id=fs.lease.owner_id,
                class_id=class_id,
                job_id=fs.lease.job_id,
                role="assistant",
                body=revised.note,
                base_revision_id=base_id,
                result_revision_id=revision_id,
                change=change,
            )
            return revision_id
        except chat.MessageRefused:
            raise JobFailure("internal") from None
        finally:
            step.close()

    return Complete(store)


REVISE_DRAFT: Final = KINDS.register(
    JobKind(
        KIND,
        ReviseDraftInput,
        STAGES,
        run,
        consumes_credit=False,
        admit=admit,
        server_fields=frozenset({"base_revision_hash", "request_message_id"}),
    )
)
