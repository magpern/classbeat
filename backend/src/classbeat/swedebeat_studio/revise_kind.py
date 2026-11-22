"""The ``studio_revise`` job kind (ADR 0036 section 4.5, SR C5): a studio class revised by chat. The
workout, the cuts, the order and the crossfades may change; the songs may not (Change songs is
the studio's pick). **No download and no match**: the mix is recomposed from the user's cached
analyses. No credit; ``admit`` is ``design_admit``'s.

1. **The base** (one fenced read through the job's own class, ``chat.load_base``): a studio draft
   whose stored hash is the one ``admit`` stored and still the SHA-256 of ``{content, mix_spec}``
   (``internal`` otherwise: a revision is immutable), its approved list still loading, the studio
   on (``feature_off``) and the class still the studio's.
2. **Stage ``tracks``**: the bearer of the job's connection (``not_connected``), and the list's
   analyses **from the cache only** (``design_step.cached_maps``; one that is gone
   is ``music_changed``). Nothing is sent to the gateway.
3. The request's stated values layered onto the brief (``revise_stated``, as ``revise_draft``),
   then **stage ``revise``**: the studio stage over the brief, the tracks, the base draft, the base
   mix by index and the user's requests along the base's chain (no earlier note or model text);
   for a **re-plan** (ADR 0036 B6, #1372) also the input's ``choices``, read again and checked
   against the list (``internal`` otherwise: admit checked them), stated first by index. The
   result is an ordinary revise, a new proposal; ``apply_mix_choices`` applies the choices to it.
   A base with an **edit overlay** (ADR 0042 sections 3 and 8, #1391; the overlay read and its
   hash checked in step 1) states the instructor's locks and overrides as fixed in the message,
   and each submitted draft is reconciled with it (``revise_overlay``: a lock breach is
   ``overlay_violated``, a correction round); the new revision stores the reconciled overlay.
4. **Store**, in the final fenced transaction: the connection held, the class still the studio's,
   the revision (parent = base, ``origin = revise_draft``; the same draft and mix again are
   ``invalid_output``), the compare-and-set of ``current_revision_id`` and the assistant message
   (the model's note and the backend's change summary).
"""

import json
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.agent import Brief, JobStageHost, run_stage
from classbeat.agent.stored_draft import stored_draft
from classbeat.agent.studio_design import StudioBase, StudioContext, StudioResult, studio_stage
from classbeat.connections import hold
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.design import chat, diff, music_kind, revise_base, revise_stated, sport
from classbeat.design.edit_overlay_model import EditOverlay, parsed
from classbeat.design.interpreted import with_interpretation
from classbeat.design.kind import current_runtime, group_model
from classbeat.design.revision import StudioRevisionMusic
from classbeat.design.revision_hash import revision_hash
from classbeat.design.sport_rules import rules_for
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.plan_gates import PlanGates
from classbeat.quota.route import with_plan, with_plan_run
from classbeat.studio import mix_choice_model
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_match.kind import open_match_bearer
from classbeat.swedebeat_studio import design_step, draft, revise_overlay, song_list
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.design_admit import admit_revise
from classbeat.swedebeat_studio.design_inputs import StudioReviseInput

KIND: Final = "studio_revise"
STAGES: Final = ("tracks", "revise")

_BASE_MUSIC = text(
    "SELECT r.music, r.edit_overlay FROM class_revisions r JOIN jobs j ON j.owner_id = r.owner_id "
    "AND j.class_id = r.class_id WHERE j.id = :job_id AND j.owner_id = :owner_id "
    "AND r.id = :base AND r.kind = 'draft'"
)


async def _base(
    job_run: JobRun, given: StudioReviseInput, context: int
) -> tuple[chat.BaseForRun, StudioRevisionMusic, song_list.StudioList, EditOverlay]:
    if given.base_revision_hash is None:
        raise JobFailure("internal")
    params = {"job_id": job_run.lease.job_id, "owner_id": job_run.lease.owner_id}
    async with job_run.fenced() as fs:
        owner = fs.lease.owner_id
        on = await studio_on(fs.db) and await uses_swedebeat_in(fs.db, owner)
        if on:
            await music_kind.still_studio(fs, given.the_class)
        row = await chat.load_base(fs.db, owner, fs.lease.job_id, given.base_revision_id, context)
        raw = (await fs.db.execute(_BASE_MUSIC, {**params, "base": given.base_revision_id})).first()
        music: StudioRevisionMusic | None = None
        stored = None
        try:
            music = (
                None
                if raw is None
                else StudioRevisionMusic.model_validate_json(json.dumps(raw.music))
            )
        except ValidationError:
            music = None
        if music is not None:
            stored = await song_list.load(fs.db, owner, given.the_class, music.song_list_id)
    if not on:
        raise JobFailure("feature_off")
    if row is None or row.kind != "draft" or not row.brief or music is None or stored is None:
        raise JobFailure("invalid_input")
    base_hash = bytes.fromhex(given.base_revision_hash)
    spec = music.mix_spec.model_dump(mode="json")
    overlay = None if raw is None else raw.edit_overlay
    hashed = revision_hash(row.content, mix_spec=spec, overlay=overlay)
    if row.content_hash != base_hash or hashed != base_hash:
        raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
    try:
        return row, music, stored, parsed(overlay)
    except ValueError:
        raise JobFailure("internal") from None


def _reconcile(
    base: dict[str, Any],
    overlay: EditOverlay,
    music: draft.StudioMusic,
    base_music: StudioRevisionMusic,
    gates: PlanGates | None,
) -> revise_overlay.Reconcile | None:
    """The overlay's reconciliation of each submitted draft; ``None`` without an overlay."""
    if not overlay.records:
        return None
    analysed = {a.reference: a.sha256 for a in base_music.analyses or ()}
    return revise_overlay.reconciler(base, overlay, music.references, analysed, gates)


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if (
        runtime is None
        or not isinstance(given, StudioReviseInput)
        or job_run.approved
        or given.request_message_id is None
    ):
        raise JobFailure("internal")
    row, base_music, stored, overlay = await _base(job_run, given, runtime.context_requests)
    try:
        brief = Brief.model_validate(row.brief)
        base_draft, retired = revise_base.without_retired_games(
            stored_draft(row.content, brief.sport)
        )
    except ValidationError:
        raise JobFailure("internal") from None
    if brief.sport != sport.CYCLING:
        raise JobFailure("internal")
    model = await group_model(runtime, job_run, given.route_key, "design")  # before any call
    await job_run.stage("tracks")
    bearer = await open_match_bearer(job_run, match_config.current(), given.swedebeat_connection_id)
    maps = await design_step.cached_maps(job_run, bearer, stored, base_music)  # no download
    music = draft.StudioMusic.of(stored.content, maps)
    choices = None
    if given.choices is not None:  # a re-plan: admit checked them against this very list
        try:
            choices = mix_choice_model.on_list(given.choices, len(music.references))
        except mix_choice_model.MixChoiceRefused:
            raise JobFailure("internal") from None
    base_plan = music.plan_of(base_music.mix_spec)
    rules = rules_for(brief, row.dismissed_rules)
    stated = await revise_stated.layered(job_run, model, brief, given.request, given.plan_gates)
    await job_run.stage("revise")
    base_content = base_draft.model_dump(mode="json", exclude_none=True)
    base = StudioBase(
        base_content,
        base_plan,
        row.earlier_requests,
        given.request,
        choices,
        fixed=revise_overlay.fixed_section(base_content, overlay),
        reconcile=_reconcile(base_content, overlay, music, base_music, given.plan_gates),
    )
    ctx = StudioContext(stated.brief, music.tracks, rules, given.plan_gates, base)
    outcome = await run_stage(
        JobStageHost(job_run), model.provider, studio_stage(ctx), ctx.first_message(), model.agent
    )
    if not isinstance(outcome.output, StudioResult):
        raise JobFailure("internal")
    revised = outcome.output
    built = draft.build(
        revised,
        outcome,
        music,
        base_music.song_list_id,
        origin="revise_draft",
        base_revision_id=given.base_revision_id,
        base_hash=row.content_hash,
        request_message_id=given.request_message_id,
        rules=rules.provenance() if rules else None,
    )
    built = with_interpretation(
        built,
        stated.brief,
        revised.result.unmet,
        given.plan_gates,
        extra=stated.items,
        stated=bool(brief.explicit),
    )
    change: dict[str, Any] = revise_base.noted(
        diff.summarize(row.content, built.content, brief.sport), retired
    )

    async def store(fs: FencedSession) -> UUID:
        if not await hold(fs.db, fs.lease.ctx, bearer.connection):
            raise JobFailure("not_connected")
        await music_kind.check_stored(fs, soundless=False, studio=True)
        revision_id = await built.store(fs)
        params = {
            "revision_id": revision_id,
            "job_id": fs.lease.job_id,
            "owner_id": fs.lease.owner_id,
            "base": given.base_revision_id,
        }
        await fs.db.execute(revise_base.CAS, params)  # no row: kept, but not current
        try:
            await chat.insert_message(
                fs.db,
                owner_id=fs.lease.owner_id,
                class_id=given.the_class,
                job_id=fs.lease.job_id,
                role="assistant",
                body=revised.note,
                base_revision_id=given.base_revision_id,
                result_revision_id=revision_id,
                change=change,
            )
        except chat.MessageRefused:
            raise JobFailure("internal") from None
        return revision_id

    return Complete(store)


STUDIO_REVISE: Final = KINDS.register(
    JobKind(
        KIND,
        StudioReviseInput,
        STAGES,
        with_plan_run(run),
        consumes_credit=False,
        admit=with_plan(admit_revise),
        server_fields=frozenset(
            {"base_revision_hash", "request_message_id", "route_key", "plan_gates"}
        ),
    )
)
