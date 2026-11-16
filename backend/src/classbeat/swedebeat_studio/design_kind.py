"""The ``studio_design`` job kind (ADR 0036 sections 1 and 4, SR C5): a studio class's design on
its approved song list. **No credit** and no MOWL connection; ``admit`` is ``design_admit``'s.

1. **Before any call** (one fenced read): the studio on and the user's SwedeBeat preference
   (``feature_off``), the class still the studio's, the list still approved, the newest, not
   designed yet and without a gap (``invalid_input``: admit's refusals as one job category). Then
   the ``design`` group's model (``model_config`` before anything is sent).
2. **Stage ``tracks``**: the SwedeBeat bearer of the job's connection, still the list's
   (``not_connected``), and ``track_analysis.analyse_tracks`` over the list's references: the
   user's cache, then the misses downloaded and analysed (C4; the slot and the daily limit there).
   A new pick after a design analyses only the references that are new.
3. **Stage ``design``**: the studio stage (``agent.studio_design``) over the brief and the tracks
   by index: the model proposes the workout and the mix (order, cuts, crossfades); the stage
   checks the mix (``mix_plan_policy``), composes it (``mowl.compose.compose_map``) and fits the
   workout (``fit_to_music``).
4. **Store**, in the final fenced transaction: the SwedeBeat connection still held
   (``not_connected``), the class still the studio's and the list still undesigned, the draft
   (``swedebeat_studio.draft``: the plan by reference, checked again against the analyses; its
   ``content_hash`` over ``{content, mix_spec}``), then the class's brief and source on its first
   design, or the new draft current on a newer list.

Registered in ``classbeat.jobs.KINDS`` on import, by the worker and the web app.
"""

from typing import Final
from uuid import UUID

from classbeat.agent import JobStageHost, run_stage
from classbeat.agent.studio_design import StudioContext, StudioResult, studio_stage
from classbeat.connections import hold
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.design import music_kind, sport
from classbeat.design.interpreted import with_interpretation
from classbeat.design.kind import current_runtime, group_model
from classbeat.design.sport_rules import rules_for
from classbeat.design.track_analysis import analyse_tracks
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.quota.route import with_plan, with_plan_run
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_match.kind import open_match_bearer
from classbeat.swedebeat_studio import design_step, draft
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.design_admit import admit_design
from classbeat.swedebeat_studio.design_inputs import StudioDesignInput

KIND: Final = "studio_design"
STAGES: Final = ("tracks", "design")


async def _still_designable(fs: FencedSession, given: StudioDesignInput) -> None:
    owner, class_id = fs.lease.owner_id, given.the_class
    await music_kind.still_studio(fs, class_id)
    found = await design_step.approved_list(fs.db, owner, class_id, given.song_list_revision_id)
    if found is None or not found.approved or not found.newest or found.designed:
        raise JobFailure("invalid_input")  # admit's refusals, as one job category


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, StudioDesignInput) or job_run.approved:
        raise JobFailure("internal")
    if given.brief is None or given.brief.sport != sport.CYCLING:
        raise JobFailure("internal")  # admit sets a cycling brief
    brief, list_id = given.brief, given.song_list_revision_id
    async with job_run.fenced() as fs:
        on = await studio_on(fs.db) and await uses_swedebeat_in(fs.db, fs.lease.owner_id)
        stored = None
        if on:
            await _still_designable(fs, given)
            stored = await design_step.load_approved(
                fs.db, fs.lease.owner_id, given.the_class, list_id
            )
    if not on:
        raise JobFailure("feature_off")
    if stored is None:
        raise JobFailure("invalid_input")
    model = await group_model(runtime, job_run, given.route_key, "design")  # before any call
    await job_run.stage("tracks")
    cfg = match_config.current()
    bearer = await open_match_bearer(job_run, cfg, given.swedebeat_connection_id)
    design_step.pinned(bearer, stored)
    if cfg.swedebeat_gateway_key is None:  # open_match_bearer checked the gateway
        raise JobFailure("internal")
    maps = await analyse_tracks(
        job_run, bearer, cfg.swedebeat_gateway_key, design_step.references(stored)
    )
    music = draft.StudioMusic.of(stored.content, maps)
    await job_run.stage("design")
    rules = rules_for(brief)
    ctx = StudioContext(brief, music.tracks, rules, given.plan_gates)
    outcome = await run_stage(
        JobStageHost(job_run), model.provider, studio_stage(ctx), ctx.first_message(), model.agent
    )
    if not isinstance(outcome.output, StudioResult):
        raise JobFailure("internal")
    built = draft.build(
        outcome.output, outcome, music, list_id, rules=rules.provenance() if rules else None
    )
    built = with_interpretation(built, brief, outcome.output.result.unmet, given.plan_gates)

    async def store(fs: FencedSession) -> UUID:
        if not await hold(fs.db, fs.lease.ctx, bearer.connection):
            raise JobFailure("not_connected")
        await _still_designable(fs, given)
        await music_kind.check_stored(fs, soundless=False, studio=True)
        revision_id = await built.store(fs)
        await design_step.adopt(fs, revision_id, brief.stored())
        await sport.check_stored(fs, brief.sport)
        await music_kind.check_stored(fs, soundless=False, studio=True)
        return revision_id

    return Complete(store)


STUDIO_DESIGN: Final = KINDS.register(
    JobKind(
        KIND,
        StudioDesignInput,
        STAGES,
        with_plan_run(run),
        consumes_credit=False,  # the studio pick's credit (C3): the design never charges again
        admit=with_plan(admit_design),
        server_fields=frozenset({"route_key", "plan_gates", "brief"}),
    )
)
