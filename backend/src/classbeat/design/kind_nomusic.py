"""The two ``design_draft`` runs with no music step (split out of ``classbeat.design.kind``, the
repository's split-on-touch rule): a soundless class (#527) and the composer's sketch (ADR 0038
section 4, #1186). Neither makes a MOWL or Spotify call; both run the soundless ``design`` stage
over the brief alone (``classbeat.agent.soundless``), on a model the caller checked covers
soundless (``music_kind.require_covered``), and store with a ``NoMusicStep``.

- **Soundless**: the revision's music is ``{"source": "none"}`` and the class's first
  ``music_source`` the same (``music_kind.check_stored`` before and after ``adopt_first``).
- **Sketch** (``mode = "sketch"``, set only by the interpretation hand-off for a composer class
  with music): time-based segments, as a soundless design, with provenance ``origin = "sketch"``
  and no base. Its revision's music is ``{"source": "sketch"}`` (``SKETCH_MUSIC``), so the class
  is never read as soundless (``music_kind.refuse_soundless``) and a studio or soundless job is
  refused on it. In the final fenced transaction: the revision, ``check_stored(soundless=False)``
  (never over a ``none`` or studio source), then ``adopt_sketch``: the class's ``brief`` and
  ``current_revision_id``, compare-and-set from no current revision and no music source, leaving
  ``music_source`` unset (it is set when the songs are in, ADR 0038 section 7). A class designed
  some other way meanwhile fails the job ``invalid_input``, rolled back. Then the sport is checked.
  No ``class_ready`` mail: a sketch is not a class ready to teach.
"""

import dataclasses
import json
from typing import TYPE_CHECKING, Final
from uuid import UUID

from sqlalchemy import text

from classbeat.agent import run_stage
from classbeat.agent.design import DesignResult
from classbeat.agent.records import JobStageHost
from classbeat.agent.soundless import SoundlessContext, soundless_design_stage
from classbeat.design import music_kind, revision, sport
from classbeat.design.interpreted import with_interpretation
from classbeat.design.revision_ready_policy import SKETCH
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun
from classbeat.models.jobs import GroupModel

if TYPE_CHECKING:
    from classbeat.design.kind import DesignDraftInput

SKETCH_MUSIC: Final[dict[str, str]] = {"source": SKETCH}  # a sketch revision's ``music``

# The job's own class (read from the fenced job row, never from the input), only while it has no
# current revision and no music source: the composer's first design.
_ADOPT_SKETCH = text(
    "UPDATE classes c SET current_revision_id = :revision_id, brief = CAST(:brief AS jsonb) "
    "FROM jobs j WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id IS NULL AND c.music_source IS NULL "
    "RETURNING c.id"
)


async def adopt_sketch(fs: FencedSession, revision_id: UUID, brief: dict[str, object]) -> bool:
    """Make the sketch the class's current revision and record its brief, if the class has
    neither a current revision nor a music source yet. Returns whether it did."""
    params = {
        "revision_id": revision_id,
        "brief": json.dumps(brief, sort_keys=True, ensure_ascii=False),
        "job_id": fs.lease.job_id,
        "owner_id": fs.lease.owner_id,
    }
    return (await fs.db.execute(_ADOPT_SKETCH, params)).first() is not None


async def run_no_music(job_run: JobRun, given: "DesignDraftInput", model: GroupModel) -> Complete:
    """A soundless design, or a sketch when ``given.mode`` says so. No rules section
    (``classbeat.agent.soundless_input`` says why)."""
    if given.brief.sport != sport.CYCLING:
        raise JobFailure("internal")  # the input refuses a running brief with no music
    sketch = given.mode == SKETCH
    step = music_kind.NoMusicStep()
    ctx = SoundlessContext(given.brief, given.plan_gates)  # #496
    outcome = await run_stage(
        JobStageHost(job_run),
        model.provider,
        soundless_design_stage(ctx),
        ctx.first_message(),
        model.agent,
    )
    if not isinstance(outcome.output, DesignResult):
        raise JobFailure("internal")
    draft = revision.build(
        outcome.output, outcome, None, step, origin=SKETCH if sketch else "design_draft"
    )
    draft = with_interpretation(draft, given.brief, outcome.output.unmet, given.plan_gates)
    brief = given.brief.stored()
    if sketch:
        draft = dataclasses.replace(draft, music=dict(SKETCH_MUSIC))

        async def store_sketch(fs: FencedSession) -> UUID:
            revision_id = await draft.store(fs)
            await music_kind.check_stored(fs, soundless=False)  # never over none or the studio
            if not await adopt_sketch(fs, revision_id, brief):
                raise JobFailure("invalid_input")  # designed some other way meanwhile
            await sport.check_stored(fs, given.brief.sport)
            return revision_id

        return Complete(store_sketch, release=step.close)

    async def store(fs: FencedSession) -> UUID:
        """In the final fenced transaction: the revision, the music kind checked, the class's first
        ``music_source`` of ``none``, then the sport and the music kind checked again (any of
        them rolls it all back)."""
        await step.still_current(fs)
        revision_id = await draft.store(fs)
        # A music source is refused before ``adopt_first`` can overwrite it (#589); an unset one
        # is the first design, which ``adopt_first`` sets to ``none``.
        await music_kind.check_stored(fs, soundless=True, before_adopt=True)
        await revision.adopt_first(fs, revision_id, brief, dict(music_kind.NO_MUSIC))
        await sport.check_stored(fs, given.brief.sport)
        await music_kind.check_stored(fs, soundless=True)  # strict, for a class already adopted
        return revision_id

    return Complete(store, release=step.close)
