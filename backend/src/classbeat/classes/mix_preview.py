"""The read-only preview of a studio draft's mix choices (ADR 0036 amendment B, B3, B4 and B6,
#1369): ``POST /api/classes/{id}/mix-choices/preview`` with ``{revision_id, content_hash,
choices}``.

``preview`` reads, in one user transaction (owner-scoped, ``UserContext``), and **stores
nothing**: no job, no revision, no model, no network, no download. In order:

- ``feature_off`` unless the studio runs and the user's "I use Swedebeat" preference is on;
- the class's current studio draft (``current_draft.load``: ``not_found`` for another user's or an
  unknown class, ``no_studio_draft``, ``internal``); ``stale_base`` unless ``revision_id`` is it
  and ``content_hash`` its hash;
- its proposal and the analyses it recorded (``mix_apply.proposal_for``: ``no_proposal``,
  ``music_changed``);
- the choices applied to the proposal (``mix_apply.computed`` without the fit, after its
  ``game_retired``: the same order and
  so the same first reason as the job's): a refusal is answered in ``refusal`` (``reason``, and
  for the class length the total and the range), not as an error.

The answer: the derived spec's digest (``spec_digest``), its windows and crossfades by approved-list
**position** (never a catalogue reference), the composed total, the allowed range, the refusal or
null, and the screen's per-song ``suggestions`` (``mix_suggestion_policy``).
"""

from typing import Any
from uuid import UUID

from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.studio import mix_choice_policy, mix_suggestion_policy
from classbeat.swedebeat_studio import current_draft, mix_apply, mix_apply_calc, mix_proposal
from classbeat.swedebeat_studio.admit import studio_on

Factory = Any


def _suggestions(found: mix_proposal.Resolved) -> list[dict[str, Any]]:
    return [
        {
            "position": s.position,
            "length_sec": s.length,
            "timing_class": s.timing_class,
            "pieces": [{"start_sec": a, "end_sec": b} for a, b in s.pieces],
            "ends_on_phrase": list(s.ends_on_phrase),
            "full_only": s.full_only,
        }
        for s in mix_suggestion_policy.suggestions(found.proposal, found.music.facts())
    ]


def _form(spec: dict[str, Any], found: mix_proposal.Resolved) -> dict[str, Any]:
    at = {r: n for n, r in enumerate(found.proposal.approved) if r is not None}
    return {
        "spec_digest": mix_apply_calc.spec_digest(spec),
        "windows": [
            {"position": at[t["reference"]], "start_sec": t["start_sec"], "end_sec": t["end_sec"]}
            for t in spec["tracks"]
        ],
        "crossfades": list(spec["crossfades"]),
        "total_sec": mix_choice_policy.composed_sec(spec),
    }


async def preview(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    content_hash: str,
    choices: object,
) -> dict[str, Any] | str:
    """The preview, or a reason (see the module docstring)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await studio_on(db) or not await uses_swedebeat_in(db, ctx.user_id):
            return "feature_off"
        current = await current_draft.load(db, ctx.user_id, class_id)
        if isinstance(current, str):
            return current
        if current.revision_id != revision_id or current.content_hash.hex() != content_hash:
            return "stale_base"
        base = mix_proposal.ProposalDraft(current.revision_id, current.content, current.music)
        try:
            found = await mix_apply.proposal_for(db, ctx.user_id, class_id, base, current.songs)
        except mix_apply.MixApplyRefused as refused:
            return refused.reason
    low, high = mix_choice_policy.allowed(found.proposal.target_sec)
    answer: dict[str, Any] = {
        "revision_id": str(current.revision_id),
        "proposal_revision_id": str(found.draft.revision_id),
        "target_sec": found.proposal.target_sec,
        "allowed": {"low_sec": round(low, 2), "high_sec": round(high, 2)},
        "suggestions": _suggestions(found),
    }
    try:
        derived = mix_apply.computed(found, base, choices)
    except mix_apply.MixApplyRefused as refused:
        blank: dict[str, Any] = {
            "spec_digest": None,
            "windows": None,
            "crossfades": None,
            "total_sec": None,
        }
        blank["total_sec"] = refused.total_sec  # the class length's refusal names it
        return {**answer, **blank, "refusal": {"reason": refused.reason}}
    return {**answer, **_form(derived.spec, found), "refusal": None}
