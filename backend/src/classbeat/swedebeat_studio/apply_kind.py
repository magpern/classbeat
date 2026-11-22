"""The ``apply_mix_choices`` job kind (ADR 0036 amendment B, B4, B4a, B6 and B10, #1369): the user's
mix choices applied to the model's proposal as a new studio draft, in the ``refit_draft`` pattern
(``design/refit.py``): **no model, no credit, no ``agent_runs``, no network**, nothing written
outside classbeat. No connection: the analyses are the user's cached rows the proposal recorded.

Input: ``{class_id, base_revision_id, base_content_hash, choices}``; ``plan_gates`` is a server
field, the user's plan gates when the job is admitted (``quota.gates.current_gates``), as the
design job's.

**Create time** (``admit``, in the create transaction, the class row share-locked by
``current_draft.load``), each refusal creating nothing: ``not_ready`` for a sketch base;
``feature_off`` unless the studio runs and the user's "I use Swedebeat" preference is on;
``invalid_input`` unless the class's current revision is a studio draft (``current_draft``);
``stale_base`` unless the base is that draft and ``base_content_hash`` its hash; then the whole
derivation of ``mix_apply.derive`` with the gates, in the one order of B4 (``no_proposal``,
``music_changed``, ``apply_limit``, ``game_retired``, ``invalid_input``, ``mix_plan_policy``'s
reasons, ``class_length_out_of_tolerance``, ``workout_does_not_fit``, ``plan_class_too_long``,
``plan_not_allowed``, ``nothing_pending`` for a derived spec equal to the base's). So every
refusal is answered by name before a job exists.

**Run** (stage ``apply``): one fenced read through the job's own class of the base (its hash the
input's and still the SHA-256 of ``{content, mix_spec}``, else ``internal``), the studio still on
and the class still the studio's; the same derivation again under the stored gates (defence in
depth: on the same immutable rows it answers the same; a refusal ends the job with its category,
``internal`` for one that is no job category); then **one** fenced transaction: the class still
the studio's, the ``draft`` revision (``apply_revision.build``: parent the base, provenance
``{origin: mix_choices, proposal_revision_id, choices}``) and the revise compare-and-set of
``current_revision_id`` (no row: kept, but not current). A base with an edit overlay carries it
(``mix_apply``'s docstring, ADR 0042 section 8): the new draft stores the re-applied overlay and its
hash covers it (``revision_hash``).
"""

from typing import Any, ClassVar, Final, Self
from uuid import UUID

from pydantic import model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.context import UserContext
from classbeat.design import music_kind, revise_base
from classbeat.design.revise import Sha256Hex
from classbeat.design.revision_ready import revision_ready
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.plan_gates import FIELD as GATES_FIELD
from classbeat.plan_gates import PlanGates
from classbeat.quota.gates import current_gates
from classbeat.swedebeat_studio import (
    apply_revision,
    current_draft,
    mix_apply,
    mix_proposal,
    song_list,
)
from classbeat.swedebeat_studio.admit import studio_on

KIND: Final = "apply_mix_choices"
STAGES: Final = ("apply",)

_JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


class ApplyMixChoicesInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ()

    base_revision_id: UUID
    base_content_hash: Sha256Hex
    choices: dict[str, Any]
    plan_gates: PlanGates | None = None  # server field: only ``admit`` sets it

    @model_validator(mode="after")
    def _a_class_of_its_own(self) -> Self:
        if self.class_id is None:
            raise ValueError("an apply names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id


async def admit(db: AsyncSession, ctx: UserContext, parsed: Any) -> Admitted | Refused:
    if not isinstance(parsed, ApplyMixChoicesInput):
        return Refused("invalid_input")
    class_id, base_id = parsed.the_class, parsed.base_revision_id
    not_ready = await revision_ready(db, ctx.user_id, class_id, base_id)
    if not_ready is not None:
        return Refused(not_ready)
    if not await studio_on(db) or not await uses_swedebeat_in(db, ctx.user_id):
        return Refused("feature_off")
    current = await current_draft.load(db, ctx.user_id, class_id, lock=True)
    if isinstance(current, str):
        return Refused("internal" if current == "internal" else "invalid_input")
    if current.revision_id != base_id or current.content_hash.hex() != parsed.base_content_hash:
        return Refused("stale_base")
    _, gates = await current_gates(db, ctx)
    base = mix_proposal.ProposalDraft(
        current.revision_id, current.content, current.music, current.overlay
    )
    try:
        await mix_apply.derive(
            db, ctx.user_id, class_id, base, current.songs, parsed.choices,
            limit=True, fit=True, gates=gates,
        )  # fmt: skip
    except mix_apply.MixApplyRefused as refused:
        return Refused(refused.reason, refused.counts)
    except ValueError:
        return Refused("internal")
    return Admitted({GATES_FIELD: gates.model_dump(mode="json")})


async def _derived(job_run: JobRun, given: ApplyMixChoicesInput) -> mix_apply.Derivation:
    """The fenced read and the derivation again (module docstring, Run)."""
    failure: str | None = None
    derivation: mix_apply.Derivation | None = None
    async with job_run.fenced() as fs:
        owner = fs.lease.owner_id
        params = {"job_id": fs.lease.job_id, "owner_id": owner}
        own = (await fs.db.execute(_JOB_CLASS, params)).scalar_one_or_none()
        if own != given.the_class:
            raise JobFailure("internal")
        if not (await studio_on(fs.db) and await uses_swedebeat_in(fs.db, owner)):
            raise JobFailure("feature_off")
        await music_kind.still_studio(fs, given.the_class)
        base = await mix_proposal.load(fs.db, owner, given.the_class, given.base_revision_id)
        if base is None:
            raise JobFailure("internal")  # a studio draft is immutable: its hash must hold
        stored_hash = await _hash(fs, given)
        if stored_hash != given.base_content_hash:
            raise JobFailure("internal")
        songs = await song_list.load(fs.db, owner, given.the_class, base.music.song_list_id)
        if songs is None:
            raise JobFailure("internal")
        try:
            derivation = await mix_apply.derive(
                fs.db, owner, given.the_class, base, songs, given.choices,
                fit=True, gates=given.plan_gates,
            )  # fmt: skip
        except mix_apply.MixApplyRefused as refused:
            failure = refused.reason
        except ValueError:
            failure = "internal"
    if failure is not None or derivation is None:
        raise JobFailure(failure or "internal")  # an unknown category is ``internal``
    return derivation


_HASH = text(
    "SELECT content_hash FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :id AND kind = 'draft'"
)


async def _hash(fs: FencedSession, given: ApplyMixChoicesInput) -> str | None:
    params = {
        "owner_id": fs.lease.owner_id,
        "class_id": given.the_class,
        "id": given.base_revision_id,
    }
    found = (await fs.db.execute(_HASH, params)).scalar_one_or_none()
    return None if found is None else bytes(found).hex()


async def run(job_run: JobRun) -> Complete:
    given = job_run.input
    if not isinstance(given, ApplyMixChoicesInput) or job_run.approved or given.plan_gates is None:
        raise JobFailure("internal")  # the gates are a server field: an input without is not ours
    await job_run.stage("apply")
    derivation = await _derived(job_run, given)
    made = derivation.fitted or derivation.carried
    if made is None:
        raise JobFailure("internal")
    resolved = derivation.resolved
    built = apply_revision.build(
        made,
        resolved.music,
        resolved.draft.music.song_list_id,
        derivation.choices,
        proposal_revision_id=resolved.draft.revision_id,
        base_revision_id=given.base_revision_id,
    )

    async def store(fs: FencedSession) -> UUID:
        """The class still the studio's, the revision, the compare-and-set."""
        await music_kind.check_stored(fs, soundless=False, studio=True)
        revision_id = await built.store(fs)
        params = {
            "revision_id": revision_id,
            "job_id": fs.lease.job_id,
            "owner_id": fs.lease.owner_id,
            "base": given.base_revision_id,
        }
        await fs.db.execute(revise_base.CAS, params)  # no row: kept, but not current
        return revision_id

    return Complete(store)


APPLY_MIX_CHOICES: Final = KINDS.register(
    JobKind(
        KIND,
        ApplyMixChoicesInput,
        STAGES,
        run,
        consumes_credit=False,
        admit=admit,
        server_fields=frozenset({GATES_FIELD}),
    )
)
