"""The ``refit_draft`` job kind (ADR 0007 amendment "beat-one corrections", item 6, BC3): the
class's current draft fitted again to its music with the class's beat-one corrections. No model,
no credit, no cost, no ``agent_runs``; nothing is written outside classbeat.

Input: ``{class_id, base_revision_id, mowl_connection_id}`` (``RefitDraftInput``), plus
``base_revision_hash``, which only ``admit`` sets.

**Create time** (``admit``, in the create transaction, the class row locked by
``chat.create_state``): ``invalid_input`` unless the base is a ``draft`` of the class;
``stale_base`` unless it is the class's current revision; the running flag
(``sport.admit_class``); ``refit_limit`` (``MAX_REFITS`` refit revisions per class); for a class
on a Swedebeat mix, its flag and analysis limit (``mixadmit.admit_class_mix``). The one open job
per class and the plan's concurrent jobs are the jobs API's, as for every kind.

**Pipeline** (stages ``music``, ``fit``):

1. one fenced read of the class's brief, music source and the base through the job's own class;
   the base's hash is checked against the one ``admit`` stored and against its content;
2. the class's music step (``kind.music_step``, unchanged checks, and the corrections for the
   step's fingerprint); a fingerprint other than the base's ends the job ``music_changed`` with
   nothing stored (the music is not what the base was designed on: a chat revision designs on
   the new music);
3. the base's stored content fitted again (``agent.design.check``, for a running class
   ``agent.running.check_running``) to the corrected map: the total is kept and each change moves
   to the corrected grid within the fit's tolerance;
4. the final fenced transaction: the connection guard, the revision (parent = base, provenance
   ``origin: beat_one``; stored even when the content is the base's, because its
   ``music.beat_one`` records what is applied) and the revise compare-and-set of
   ``current_revision_id``.
"""

import hashlib
import json
from typing import Annotated, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, StringConstraints, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import Brief, mix_input
from classbeat.agent.design import DesignResult, check
from classbeat.agent.running import check_running
from classbeat.agent.schemas import RunningDraft, stored_draft
from classbeat.agent.stage import ToolError
from classbeat.context import UserContext
from classbeat.design import chat, mixadmit, revision, sport
from classbeat.design.kind import (
    CLASS_MUSIC_SOURCE,
    SwedebeatMusicChoice,
    current_runtime,
    music_step,
)
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused

KIND: Final = "refit_draft"
STAGES: Final = ("music", "fit")
MAX_REFITS: Final = 50  # refit revisions per class (409 ``refit_limit``)

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class RefitDraftInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    base_revision_id: UUID
    mowl_connection_id: UUID
    base_revision_hash: Sha256Hex | None = None  # server field: only ``admit`` sets it

    @model_validator(mode="after")
    def _a_class_of_its_own(self) -> Self:
        if self.class_id is None:
            raise ValueError("a refit names its class")
        return self


_REFITS = text(
    "SELECT count(*) FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'draft' AND provenance ->> 'origin' = 'beat_one'"
)

# Through the job's own class (never the input's id alone).
_BASE = text(
    "SELECT c.brief, c.music_source, r.kind, r.content, r.content_hash, r.music "
    "FROM jobs j JOIN classes c ON c.owner_id = j.owner_id AND c.id = j.class_id "
    "JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id AND r.id = :base "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)

_CAS = text(
    "UPDATE classes c SET current_revision_id = :revision_id, updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id = :base RETURNING c.id"
)


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    if not isinstance(parsed, RefitDraftInput) or parsed.class_id is None:
        raise ValueError("admit gets this kind's input")
    class_id = parsed.class_id
    state = await chat.create_state(db, ctx.user_id, class_id, parsed.base_revision_id)
    if not state.exists or state.base_kind != "draft" or state.base_hash is None:
        return Refused("invalid_input")  # a foreign or unknown id answers like any other
    if state.current != parsed.base_revision_id:
        return Refused("stale_base")
    running = await sport.admit_class(db, ctx.user_id, class_id)
    if running is not None:
        return running
    done = (await db.execute(_REFITS, {"owner_id": ctx.user_id, "class_id": class_id})).scalar_one()
    if done >= MAX_REFITS:
        return Refused("refit_limit", {"limit": MAX_REFITS, "count": int(done)})
    mix = await mixadmit.admit_class_mix(db, ctx, class_id, parsed.mowl_connection_id)
    if mix is not None:
        return mix
    return Admitted({"base_revision_hash": state.base_hash.hex()})


def refit(draft: object, m: dict[str, object]) -> DesignResult:
    """The stored draft fitted to ``m`` with its sport's checks; ``internal`` if it no longer
    fits (a stored draft was valid when it was stored)."""
    try:
        if isinstance(draft, RunningDraft):
            return check_running(draft, m)  # pyright: ignore[reportArgumentType]
        return check(draft, m)  # pyright: ignore[reportArgumentType]
    except ToolError:
        raise JobFailure("invalid_output") from None


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if (
        runtime is None
        or not isinstance(given, RefitDraftInput)
        or job_run.approved
        or given.class_id is None
        or given.base_revision_hash is None
    ):
        raise JobFailure("internal")
    base_id = given.base_revision_id
    async with job_run.fenced() as fs:
        row = (
            await fs.db.execute(
                _BASE,
                {"base": base_id, "job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id},
            )
        ).first()
    if row is None or row.kind != "draft" or not row.brief or not row.music_source:
        raise JobFailure("invalid_input")
    base_hash = bytes.fromhex(given.base_revision_hash)
    if (
        bytes(row.content_hash) != base_hash
        or hashlib.sha256(revision.canonical(row.content)).digest() != base_hash
    ):
        raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
    try:
        brief = Brief.model_validate(row.brief)
        music = CLASS_MUSIC_SOURCE.validate_python(row.music_source)
        base_draft = stored_draft(row.content, brief.sport)
        base_music = revision.REVISION_MUSIC.validate_json(json.dumps(row.music))
    except ValidationError:
        raise JobFailure("internal") from None
    if brief.sport == sport.RUNNING:
        if isinstance(music, SwedebeatMusicChoice):
            raise JobFailure("internal")  # running v1 has no mix (ADR 0018)
        await sport.still_on(job_run)  # before anything is called
    if isinstance(music, SwedebeatMusicChoice):
        await mixadmit.still_on(job_run, runtime.swedebeat_enabled)  # before anything is called
    await job_run.stage("music")
    step = await music_step(job_run, runtime, music, given.mowl_connection_id)
    handed_over = False
    try:
        if step.fingerprint != base_music.fingerprint:
            raise JobFailure("music_changed")
        await job_run.stage("fit")
        m = mix_input.music_map(step.data)
        result = refit(base_draft, m)
        song_list_id = getattr(base_music, "song_list_id", None)
        draft = revision.build_refit(
            result, m, step, base_revision_id=base_id, song_list_id=song_list_id
        )

        async def store(fs: FencedSession) -> UUID:
            """The connection guard, the revision, the compare-and-set."""
            await step.still_current(fs)
            revision_id = await draft.store(fs)
            params = {
                "revision_id": revision_id,
                "job_id": fs.lease.job_id,
                "owner_id": fs.lease.owner_id,
                "base": base_id,
            }
            await fs.db.execute(_CAS, params)  # no row: kept, but not current
            return revision_id

        complete = Complete(store, release=step.close)
        handed_over = True
        return complete
    finally:
        if not handed_over:
            step.close()


REFIT_DRAFT: Final = KINDS.register(
    JobKind(
        KIND,
        RefitDraftInput,
        STAGES,
        run,
        consumes_credit=False,
        admit=admit,
        server_fields=frozenset({"base_revision_hash"}),
    )
)
