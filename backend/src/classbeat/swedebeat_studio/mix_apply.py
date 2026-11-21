"""The derivation of a studio draft from the user's mix choices, read and computed in the caller's
transaction (ADR 0036 amendment B, B4, B4a and B6, #1369): the read-only preview
(``classes.mix_preview``), the ``apply_mix_choices`` job's ``admit`` and its run share it, so the
order of the checks and the first reason are the same in all three. It **reads only**: nothing
is stored, no model, no network, no download.

``derive``, in the one order of B4 (each step's refusal is a ``MixApplyRefused`` with its name):

1. the proposal of the base draft (``mix_proposal.for_draft``: the allowlist of B4):
   ``no_proposal``; its analyses as recorded, from the cache: ``music_changed``;
2. with ``limit`` (the job): at most ``MAX_CHOICE_APPLIES`` applied drafts per proposal
   (``apply_limit``); with ``fit``, a proposal naming a game MOWL no longer offers is
   ``game_retired`` (ADR 0029 section 3, as ``refit_draft``);
3. the choices (``mix_choice_model.parsed``): ``invalid_input``;
4. ``mix_apply_calc.derived``: ``invalid_input`` (a position off the list), ``checked``'s
   reasons, ``class_length_out_of_tolerance``;
5. with ``fit`` (the job): ``mix_apply_calc.fitted``: ``workout_does_not_fit``,
   ``plan_class_too_long``, ``plan_not_allowed``; then a derived spec equal to the base's stores
   nothing (``nothing_pending``).
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.games_policy import RETIRED, has_retired_game
from classbeat.plan_gates import PlanGates
from classbeat.studio import mix_choice_model
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices
from classbeat.swedebeat_studio import mix_apply_calc, mix_proposal, song_list
from classbeat.swedebeat_studio.mix_apply_calc import Fitted

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
    if fit and has_retired_game(found.draft.content):
        raise MixApplyRefused(RETIRED)  # ADR 0029: a retired game is never fitted or stored again
    try:
        choices = mix_choice_model.parsed(raw_choices)
        spec = mix_apply_calc.derived(found.proposal, found.music, choices)
        fitted = None
        if fit:
            fitted = mix_apply_calc.fitted(
                found.draft.content, found.draft.spec, spec, found.music, gates
            )
    except MixChoiceRefused as exc:
        raise _refused(exc) from None
    if fit and mix_apply_calc.stored_spec(spec) == base.spec:
        raise MixApplyRefused(NOTHING_PENDING)
    return Derivation(found, choices, spec, fitted)


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
