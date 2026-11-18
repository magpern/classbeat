"""The first real draft of a composer class (ADR 0038 section 7, #1189), for
``classbeat.songs.composer_phase2``: the approved preview re-timed with no model, the run that
designed it, and the final transaction's compare-and-set from the sketch.

- ``retimed``: the approved preview's content fitted to the final playlist's map with
  ``fit_to_music`` (``agent.design.check``: validate, fit, validate again) and within the job's
  plan; ``None`` when it no longer validates, and the caller re-designs with a model instead.
- ``earlier_run``: the ``agent_runs`` row of the job's ``design`` stage that designed the preview
  (``preview.design_run_id``), as a ``StageOutcome`` for the draft's provenance. It must be the
  owner's, of the agent stage ``revise`` (what the job's ``design`` stage runs), ``submitted``,
  and a run of **the job that stored the approved list** (a resume is another job, so it is
  never "this job's"); ``None`` otherwise, and the caller
  re-designs rather than name a run it cannot prove.
- ``with_fit``: the draft with its provenance's ``fit`` (``agent.records``), validated again.
- ``adopt``: in phase 2's final transaction, the draft becomes current **by compare-and-set from
  the sketch** (ADR 0007 section 2), with the class's ``music_source`` and ``save_pending`` in the
  same statement; no row (another revision is current) fails the job ``invalid_input`` and rolls
  the whole store back.
"""

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.agent import DesignResult, Provenance, WorkoutDraft
from classbeat.agent.design import check, within_plan
from classbeat.agent.loop import RunInfo, StageOutcome
from classbeat.agent.provider import Usage
from classbeat.agent.stage import ToolError
from classbeat.agent.stored_draft import stored_draft
from classbeat.design.revision import DraftRevision
from classbeat.jobs import FencedSession, JobFailure
from classbeat.plan_gates import PlanGates

_RUN = text(
    "SELECT a.id, a.provider, a.model, a.model_answered, a.effort, a.prompt_version, "
    "a.route_key, a.eval_result_id, a.turns, a.tool_calls, a.input_tokens, a.output_tokens, "
    "a.cache_read_tokens, a.cache_write_tokens, a.cost_micros FROM agent_runs a "
    "JOIN class_revisions r ON r.owner_id = a.owner_id AND r.job_id = a.job_id "
    "WHERE a.owner_id = :owner_id AND a.id = :run_id AND a.stage = 'revise' "
    "AND a.outcome = 'submitted' AND r.id = :list_id AND r.kind = 'song_list'"
)
_ADOPT = text(
    "UPDATE classes c SET current_revision_id = :revision_id, "
    "music_source = CAST(:music_source AS jsonb), save_pending = CAST(:save_pending AS jsonb), "
    "updated_at = now() FROM jobs j WHERE j.id = :job_id AND j.owner_id = :owner_id "
    "AND c.owner_id = j.owner_id AND c.id = j.class_id AND c.current_revision_id = :sketch "
    "RETURNING c.id"
)


def retimed(
    content: dict[str, Any], music_map: dict[str, Any], gates: PlanGates | None
) -> DesignResult | None:
    """The module docstring's ``retimed``."""
    try:
        draft = stored_draft(content, "cycling")
        if not isinstance(draft, WorkoutDraft):
            return None
        return within_plan(gates, check(draft, music_map))
    except (ToolError, ValidationError):
        return None


async def earlier_run(fs: FencedSession, list_id: UUID, run_id: UUID) -> StageOutcome | None:
    """The module docstring's ``earlier_run``."""
    params = {"owner_id": fs.lease.owner_id, "run_id": run_id, "list_id": list_id}
    row = (await fs.db.execute(_RUN, params)).first()
    if row is None:
        return None
    info = RunInfo(
        "revise",
        row.provider,
        row.model,
        row.effort,
        row.prompt_version,
        row.route_key,
        row.eval_result_id,
    )
    usage = Usage(
        int(row.input_tokens),
        int(row.output_tokens),
        int(row.cache_read_tokens),
        int(row.cache_write_tokens),
    )
    return StageOutcome(
        None,
        row.id,
        info,
        int(row.turns),
        int(row.tool_calls),
        usage,
        int(row.cost_micros),
        row.model_answered,
    )


def with_fit(draft: DraftRevision, fit: Literal["kept", "redesigned"]) -> DraftRevision:
    """``draft`` with ``provenance.fit``; ``internal`` if that is not a valid provenance."""
    try:
        made = Provenance.model_validate({**draft.provenance, "fit": fit}, strict=False)
    except ValidationError:
        raise JobFailure("internal") from None
    return DraftRevision(
        draft.content,
        draft.content_hash,
        draft.music,
        made.model_dump(mode="json"),
        draft.parent_id,
    )


async def adopt(
    fs: FencedSession,
    revision_id: UUID,
    content_hash: bytes,
    sketch_id: UUID,
    music_source: dict[str, Any],
    *,
    save: bool,
) -> dict[str, str] | None:
    """The module docstring's ``adopt``; returns the ``save_pending`` it stored (None: no save)."""
    pending = (
        {
            "revision_id": str(revision_id),
            "content_hash": content_hash.hex(),
            "job_id": str(fs.lease.job_id),
        }
        if save
        else None
    )
    params = {
        "revision_id": revision_id,
        "music_source": json.dumps(music_source, sort_keys=True, ensure_ascii=False),
        "save_pending": None if pending is None else json.dumps(pending, sort_keys=True),
        "job_id": fs.lease.job_id,
        "owner_id": fs.lease.owner_id,
        "sketch": sketch_id,
    }
    if (await fs.db.execute(_ADOPT, params)).first() is None:
        raise JobFailure("invalid_input")  # the sketch is no longer current: nothing is adopted
    return pending
