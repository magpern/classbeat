"""The derivation of a studio draft from the user's mix choices, read and computed in the caller's
transaction (ADR 0036 amendment B, B4, B4a and B6, #1369): the read-only preview
(``classes.mix_preview``), the ``apply_mix_choices`` job's ``admit`` and its run share it, so the
order of the checks and the first reason are the same in all three. It **reads only**: nothing
is stored, no model, no network, no download.

``derive``, in the one order of B4 (each step's refusal is a ``MixApplyRefused`` with its name):

1. the proposal of the base draft (``mix_proposal.for_draft``: the allowlist of B4):
   ``no_proposal``; its analyses as recorded, from the cache: ``music_changed``;
2. with ``limit`` (the job): at most ``MAX_CHOICE_APPLIES`` applied drafts per proposal
   (``apply_limit``); then, in the preview and the job alike, a proposal naming a game MOWL no
   longer offers is ``game_retired`` (ADR 0029 section 3, as ``refit_draft``: it is never fitted
   or stored again);
3. the choices (``mix_choice_model.parsed``): ``invalid_input``;
4. ``mix_apply_calc.derived``: ``invalid_input`` (a position off the list), ``checked``'s
   reasons, ``class_length_out_of_tolerance``;
5. with ``fit`` (the job): ``mix_apply_calc.fitted``: ``workout_does_not_fit``,
   ``plan_class_too_long``, ``plan_not_allowed``; then a derived spec equal to the base's stores
   nothing (``nothing_pending``).

**A base with an edit overlay** (ADR 0042 section 8, #1391): the overlay is carried. Step 4 derives
the spec with the overlay's cut pins (the preview's spec is the one the job stores), and step 5 is
``studio_edit_ops.carried``: the base's own workout remapped onto that spec, the overlay re-applied
by its fixed order (``locked_block_conflict``), fitted and gated as an audio edit; the result is
``Derivation.carried``, the new draft's content, spec and overlay.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.games_policy import RETIRED, has_retired_game
from classbeat.design.workout_edit_policy import EditRefused
from classbeat.plan_gates import PlanGates
from classbeat.studio import mix_choice_model
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices
from classbeat.swedebeat_studio import mix_apply_calc, mix_proposal, song_list, studio_edit_ops
from classbeat.swedebeat_studio.mix_apply_calc import Fitted
from classbeat.swedebeat_studio.studio_edit_calc import EditResult, EditState

MAX_CHOICE_APPLIES: Final = 30  # applied drafts per proposal (409 ``apply_limit``)
NOTHING_PENDING: Final = "nothing_pending"

_APPLIES = text(
    "SELECT count(*) FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'draft' AND provenance ->> 'origin' = 'mix_choices' "
    "AND provenance ->> 'proposal_revision_id' = :proposal"
)


class MixApplyRefused(Exception):
    """One named refusal; ``counts`` whole numbers for the answer (a limit, the class length)."""

    def __init__(
        self, reason: str, counts: dict[str, int] | None = None, total_sec: float | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.counts = counts or {}
        self.total_sec = total_sec


@dataclass(frozen=True)
class Derivation:
    resolved: mix_proposal.Resolved
    choices: MixChoices
    spec: dict[str, Any]  # the derived spec as ``checked`` returned it
    fitted: Fitted | None = None
    carried: EditResult | None = None  # a base with an overlay (module docstring)


def _refused(exc: MixChoiceRefused) -> MixApplyRefused:
    counts: dict[str, int] = {}
    if exc.total_sec is not None and exc.allowed is not None:
        counts = {
            "total_sec": round(exc.total_sec),
            "low_sec": round(exc.allowed[0]),
            "high_sec": round(exc.allowed[1]),
        }
    return MixApplyRefused(exc.reason, counts, exc.total_sec)


async def proposal_for(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    base: mix_proposal.ProposalDraft,
    songs: song_list.StudioList,
    *,
    limit: bool = False,
) -> mix_proposal.Resolved:
    """Steps 1 and 2: the proposal of ``base`` (a studio draft of ``songs``)."""
    list_id = base.music.song_list_id
    found = await mix_proposal.for_draft(db, owner_id, class_id, base.revision_id, list_id, songs)
    if isinstance(found, str):
        raise MixApplyRefused(found)
    if limit:
        params = {
            "owner_id": owner_id,
            "class_id": class_id,
            "proposal": str(found.draft.revision_id),
        }
        done = int((await db.execute(_APPLIES, params)).scalar_one())
        if done >= MAX_CHOICE_APPLIES:
            raise MixApplyRefused("apply_limit", {"limit": MAX_CHOICE_APPLIES, "count": done})
    return found


def computed(
    found: mix_proposal.Resolved,
    base: mix_proposal.ProposalDraft,
    raw_choices: object,
    *,
    fit: bool = False,
    gates: PlanGates | None = None,
) -> Derivation:
    """Steps 3 to 5 on the proposal ``found`` (no I/O)."""
    if has_retired_game(found.draft.content):
        raise MixApplyRefused(RETIRED)  # ADR 0029: a retired game is never fitted or stored again
    pins = base.overlay.pins()
    try:
        choices = mix_choice_model.parsed(raw_choices)
        spec = mix_apply_calc.derived(found.proposal, found.music, choices, pins)
        fitted = None
        if fit and not base.overlay.records:
            fitted = mix_apply_calc.fitted(
                found.draft.content, found.draft.spec, spec, found.music, gates
            )
    except MixChoiceRefused as exc:
        raise _refused(exc) from None
    carried = _carried(found, base, choices, gates) if fit and base.overlay.records else None
    if fit and mix_apply_calc.stored_spec(spec) == base.spec:
        raise MixApplyRefused(NOTHING_PENDING)
    return Derivation(found, choices, spec, fitted, carried)


def _carried(
    found: mix_proposal.Resolved,
    base: mix_proposal.ProposalDraft,
    choices: MixChoices,
    gates: PlanGates | None,
) -> EditResult:
    """Step 5 for a base with an overlay (module docstring); a refusal is ``MixApplyRefused``."""
    state = EditState(
        content=base.content,
        spec=base.spec,
        overlay=base.overlay,
        music=found.music,
        analysed={a.reference: a.sha256 for a in base.music.analyses or ()},
        proposal=found.proposal,
        choices=choices,
        map_version=base.music.map_version,
    )
    try:
        return studio_edit_ops.carried(state, gates)
    except EditRefused as refused:
        raise MixApplyRefused(refused.reason) from None


async def derive(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    base: mix_proposal.ProposalDraft,
    songs: song_list.StudioList,
    raw_choices: object,
    *,
    limit: bool = False,
    fit: bool = False,
    gates: PlanGates | None = None,
) -> Derivation:
    """The derivation of ``base`` (steps 1 to 5), else ``MixApplyRefused``."""
    found = await proposal_for(db, owner_id, class_id, base, songs, limit=limit)
    return computed(found, base, raw_choices, fit=fit, gates=gates)
