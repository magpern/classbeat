"""Phase 2 of a composer's song list (ADR 0038 section 7, #1189): the approved ``fill``, ``swap`` or
``change`` job, or a **composer resume** (``mode = "resume"`` with the receipt's sketch as
``base_revision_id``, made only by the hand-off ``classbeat.classes.song_resume``).

1. ``prepare``, ``create``, ``fill``, ``attach`` and ``music`` exactly as every pick's phase 2
   (``classbeat.songs.phase2.final_music``), with the sketch as the base that must still be the
   class's current revision (``invalid_input`` otherwise, before any Spotify write): the list
   approved (the job's ``approval_revision_id``, or the resume's named list, read from the
   database), still the newest and still hashing to its stored hash, the final private playlist
   verified against the approved ids in order, attached in MOWL and analysed; the analysed track
   list must be exactly the approved ids in the approved order (``playlist_mismatch``).
2. ``fit`` instead of phase 2's ``design``, called here, never as a ``refit_draft`` job (so Beat
   One's origin and ``refit_limit`` are not involved). The final analysis's spans
   (``preview_model.song_times``, the function the preview's came from) against the approved
   ``preview.music`` (``fit_policy.keeps``):
   - **kept**: the preview's content re-timed to the final map with ``fit_to_music``, no model, no
     change of shape (``composer_draft.retimed``); the draft's provenance names the run of the
     earlier ``design`` stage that designed the preview (``composer_draft.earlier_run``);
   - **otherwise** (or when the re-time no longer validates, or that run cannot be proven): the
     ``revise`` stage with a model, with **the approved preview's content as its base** and a fixed
     request (``composer_design.FIT_REQUEST``), so the approved shape is kept; its provenance names
     the new run.
3. The final fenced transaction: phase 2's ``final_checks``, the ``draft`` revision
   (``origin = "design_with_songs"``, ``music.song_list_id``, provenance ``fit``), the music kind
   and the sport checked, the **compare-and-set from the sketch** (``composer_draft.adopt``, with
   ``music_source`` and ``save_pending``), and the MOWL id of the final playlist.

A redesigned draft's ride shape is checked against the sketch's layered brief (the latest
instruction, ``composer_common.read_sketch``) by the revise stage's gate; a section still missed
after its corrections is recorded on the draft as Not as asked ``shape_off`` (``provenance.
interpretation``). A kept fit carries the approved list's own such items.

``save_pending = {revision_id, content_hash, job_id}`` is stored only when the approval had
``save: true`` (the approved job's ``approval_save``; a resume's ``save``, copied from the
receipt by its ``admit``) **and** the fit kept the preview. The worker never enqueues the save
(the session guard): the browser saves exactly that revision. A re-designed draft says so
(``provenance.fit = "redesigned"``) and is saved only by the user's own Save.

No credit is taken: the approved job and the resume are created with none (the sketch took the
class credit). Nothing here logs or sends a model an id.
"""

from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text

from classbeat.agent import mix_input
from classbeat.agent.loop import StageOutcome
from classbeat.design import corrections, music_kind, revision, sport
from classbeat.design.guidance import rule_set_for
from classbeat.design.interpreted import shape_off_items, with_items
from classbeat.design.kind import Runtime, group_model
from classbeat.design.music import MusicStep
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun
from classbeat.songs import composer_design, composer_draft, config, fit_policy, song_list
from classbeat.songs.composer_common import Sketch, brief_of, dismissed_of, read_sketch
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.phase2 import Prepared, final_music
from classbeat.songs.phase2_final import final_checks, mark_mowl, music_source
from classbeat.songs.preview_model import Preview, song_times

ORIGIN = "design_with_songs"
_SAVE = text("SELECT approval_save FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def _save_asked(run: JobRun, given: DesignWithSongsInput) -> bool:
    """The receipt's ``save``: the approved job's own row, or the resume's copy of it."""
    if not run.approved:
        return bool(given.save)
    async with run.fenced() as fs:
        return bool((await fs.db.execute(_SAVE, fs.lease.params())).scalar_one_or_none())


async def run_composer_phase_two(
    run: JobRun, given: DesignWithSongsInput, runtime: Runtime
) -> Complete:
    sketch_id = given.base_revision_id
    if sketch_id is None or given.class_id is None:
        raise JobFailure("internal")
    songs = config.current()
    save = await _save_asked(run, given)
    p, final, step = await final_music(
        run, runtime.keyring, given, songs, base_revision_id=sketch_id
    )
    handed_over = False
    try:
        preview = p.stored.content.preview
        if preview is None:
            raise JobFailure("invalid_input")  # not a composer's list: never one of this kind
        sketch = await read_sketch(run, sketch_id)  # still a sketch and still current
        brief = brief_of(sketch)
        rules = rule_set_for(brief, dismissed_of(sketch))
        await corrections.attach(run, step)  # the class's beat-one corrections (ADR 0007)
        await run.stage("fit")
        recorded = rules.provenance() if rules else None  # as phase 2's design records them
        draft = await _fitted(run, given, p, preview, step, songs.fit_tolerance_seconds, recorded)
        fit: Literal["kept", "redesigned"] = "kept"
        if draft is None:
            fit = "redesigned"
            draft = await _redesigned(run, given, runtime, p, preview, step, sketch, recorded)
        if fit == "kept":  # the approved preview's missed shape sections, shown at the approval
            carried = [i for i in p.stored.content.unmet if i.path.startswith("shape.")]
            draft = with_items(draft, carried)
        draft = composer_draft.with_fit(draft, fit)
        mowl_playlist_id = step.data.playlist_id
        kept_save = save and fit == "kept"

        async def store(fs: FencedSession) -> UUID:
            """The final transaction (module docstring, step 3)."""
            await final_checks(fs, p, final, step)
            revision_id = await draft.store(fs)
            await music_kind.check_stored(fs, soundless=False)  # never over a ``none`` source
            await composer_draft.adopt(
                fs, revision_id, draft.content_hash, sketch_id, music_source(final), save=kept_save
            )
            await sport.check_stored(fs, brief.sport)  # the class's sport never changes
            await mark_mowl(fs, p, final, mowl_playlist_id)
            return revision_id

        complete = Complete(store, release=step.close)
        handed_over = True
        return complete
    finally:
        if not handed_over:
            step.close()


async def _fitted(
    run: JobRun,
    given: DesignWithSongsInput,
    p: Prepared,
    preview: Preview,
    step: MusicStep,
    tolerance: float,
    rules: Mapping[str, Any] | None,
) -> revision.DraftRevision | None:
    """The kept draft (module docstring, step 2), or None to re-design."""
    try:
        final_songs = song_times(step.data.analysis["map"])
    except (ValueError, KeyError, TypeError):
        return None
    fingerprint = song_list.fingerprint(p.stored.track_ids)
    if not fit_policy.keeps(preview.music, fingerprint, final_songs, tolerance):
        return None
    music_map = mix_input.music_map(step.data)
    result = composer_draft.retimed(preview.content, music_map, given.plan_gates)
    if result is None:
        return None
    async with run.fenced() as fs:
        designed: StageOutcome | None = await composer_draft.earlier_run(
            fs, p.revision_id, preview.design_run_id
        )
    if designed is None:
        return None
    return revision.build(
        result,
        designed,
        music_map,
        step,
        origin=ORIGIN,
        song_list_id=p.revision_id,
        rules=rules,
    )


async def _redesigned(
    run: JobRun,
    given: DesignWithSongsInput,
    runtime: Runtime,
    p: Prepared,
    preview: Preview,
    step: MusicStep,
    sketch: Sketch,
    rules: Mapping[str, Any] | None,
) -> revision.DraftRevision:
    """The model's design on the approved preview (module docstring, step 2)."""
    model = await group_model(runtime, run, given.route_key, "design")
    outcome, revised, music_map = await composer_design.design_on(
        run,
        step,
        brief_of(sketch),
        preview.content,
        composer_design.FIT_REQUEST,
        model.provider,
        model.agent,
        config.current(),
        given.plan_gates,
        dismissed_of(sketch),
    )
    built = revision.build(
        revised.result,
        outcome,
        music_map,
        step,
        origin=ORIGIN,
        song_list_id=p.revision_id,
        rules=rules,
    )
    # A shape section the design still misses is recorded on the draft, never saved by itself:
    # ``save_pending`` stays unset for a redesigned draft (module docstring, step 3).
    return with_items(built, shape_off_items(brief_of(sketch), revised.result.unmet))
