"""The ``revise_draft`` run of a soundless class (#528), split out of ``classbeat.design.revise``.

No ``music`` stage and no MOWL or Spotify call: the switch and the model's coverage are checked
first (the worker's backstop to ``admit``), then the request's stated values are layered onto the
brief (``revise_stated``, #904), then the soundless ``revise`` stage over the brief, the base draft
and the requests, then the store with a ``NoMusicStep``. No ``songs_requested`` exit, no
``music_changed``, no rules section.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from classbeat.agent import run_stage
from classbeat.agent.records import JobStageHost
from classbeat.agent.revise import ReviseResult
from classbeat.agent.soundless import SoundlessReviseContext, soundless_revise_stage
from classbeat.design import chat, diff, music_kind, revise_base, revise_stated, revision, sport
from classbeat.design.interpreted import with_interpretation
from classbeat.design.kind import Runtime, group_model
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun

if TYPE_CHECKING:
    from classbeat.design.revise import ReviseDraftInput


async def run_soundless(
    job_run: JobRun, given: "ReviseDraftInput", loaded: revise_base.RunBase, runtime: Runtime
) -> Complete:
    if (
        given.class_id is None
        or given.request_message_id is None
        or given.base_revision_hash is None
    ):
        raise JobFailure("internal")
    base, brief, base_draft = loaded.row, loaded.brief, loaded.draft
    if brief.sport != sport.CYCLING:
        raise JobFailure("internal")  # the input refuses a running brief with no music
    class_id, base_id = given.class_id, given.base_revision_id
    request_message_id, base_hash = (
        given.request_message_id,
        bytes.fromhex(given.base_revision_hash),
    )
    music_kind.still_on()  # the switch, before anything is called
    model = await group_model(runtime, job_run, given.route_key, "design")
    music_kind.require_covered(model)  # a model evaluated for soundless only
    step = music_kind.NoMusicStep()
    stated = await revise_stated.layered(job_run, model, brief, given.request, given.plan_gates)
    ctx = SoundlessReviseContext(
        stated.brief, base_draft, base.earlier_requests, given.request, given.plan_gates
    )
    outcome = await run_stage(
        JobStageHost(job_run),
        model.provider,
        soundless_revise_stage(ctx),
        ctx.first_message(),
        model.agent,
    )
    if not isinstance(outcome.output, ReviseResult):
        raise JobFailure("internal")
    revised = outcome.output
    draft = revision.build(
        revised.result,
        outcome,
        None,
        step,
        origin="revise_draft",
        base_revision_id=base_id,
        base_hash=base_hash,
        request_message_id=request_message_id,
    )
    draft = with_interpretation(
        draft,
        stated.brief,
        revised.result.unmet,
        given.plan_gates,
        extra=stated.items,
        stated=bool(brief.explicit),
    )
    change = revise_base.noted(
        diff.summarize(base.content, draft.content, brief.sport), loaded.retired
    )

    async def store(fs: FencedSession) -> UUID:
        """The music kind, the revision, the compare-and-set, the assistant message."""
        try:
            await music_kind.check_stored(fs, soundless=True)  # the kind never changes
            revision_id = await draft.store(fs)
            params = {
                "revision_id": revision_id,
                "job_id": fs.lease.job_id,
                "owner_id": fs.lease.owner_id,
                "base": base_id,
            }
            await fs.db.execute(revise_base.CAS, params)  # no row: kept, but not current
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

    return Complete(store, release=step.close)
