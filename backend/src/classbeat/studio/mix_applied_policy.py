"""B9's two facts of a studio draft chosen for SwedeBeat outputs (ADR 0036 amendment B9, #1370).
Pure: no I/O. ``swedebeat_studio.applied_form`` reads what they take.

- ``mixed``: not ``mix_choice_policy.is_plain`` of the draft's stored, hash-checked spec, the
  lengths from the analyses the draft holds. A reference without one is mixed (fail closed).
- ``basis``: the proposal and the choices the draft must be ``applied`` of, given the proposal B4
  resolves for it (``resolved``: ``swedebeat_studio.mix_proposal.resolve``, the one walk and the
  one allowlist, ``mix_proposal_policy``). A proposal itself (its origin in the allowlist, so it
  resolves to itself) with the choices the chosen outputs imply (``implied``: whole songs without
  ``swedebeat_mix``, the DJ-mix defaults with it); an applied draft (``origin: mix_choices``,
  #1369) with its recorded ``proposal_revision_id``, which must be ``resolved``, and its recorded
  ``choices``. Any other draft, no proposal, or a record that does not read, has none (``None``).
- ``holds``: the stored spec equals ``applied(proposal, facts, choices)`` in the stored form.
  An ``applied`` that refuses, or anything that does not read, does not hold (fail closed).

Whatever choices are recorded, a spec equal to their ``applied`` keeps B5 and B6 (``applied``
enforces both), so the record can never widen what is admitted.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from classbeat.studio.mix_choice_model import MixChoices, Proposal, parsed
from classbeat.studio.mix_choice_policy import applied, is_plain
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_proposal_policy import APPLIED_ORIGIN, is_proposal
from classbeat.studio.mix_spec_model import MixSpec


@dataclass(frozen=True, slots=True)
class Basis:
    proposal_id: UUID
    choices: MixChoices


def mixed(spec: Mapping[str, Any], facts: Mapping[str, TrackFacts]) -> bool:
    """Not whole songs (module docstring)."""
    try:
        return not is_plain(spec, {r: f.length for r, f in facts.items()})
    except (ValueError, KeyError, TypeError):
        return True


def implied(outputs: Collection[str]) -> MixChoices:
    """The choices a proposal approved as it is stands for (B9)."""
    return MixChoices.dj_defaults() if "swedebeat_mix" in outputs else MixChoices.whole_songs()


def _recorded(provenance: Mapping[str, Any]) -> tuple[UUID, MixChoices] | None:
    raw = provenance.get("proposal_revision_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw), parsed(provenance.get("choices"))
    except ValueError:  # an id or choices that do not read (``MixChoiceRefused`` included)
        return None


def basis(
    draft_id: UUID,
    origin: object,
    resolved: UUID | None,
    provenance: Mapping[str, Any],
    outputs: Collection[str],
) -> Basis | None:
    """The proposal and choices the draft ``draft_id`` must be ``applied`` of, or None."""
    if resolved is None:
        return None
    if is_proposal(origin):
        return Basis(resolved, implied(outputs)) if resolved == draft_id else None
    if origin == APPLIED_ORIGIN:
        recorded = _recorded(provenance)
        if recorded is None or recorded[0] != resolved:
            return None  # not the proposal B4 resolves for this draft (any more)
        return Basis(resolved, recorded[1])
    return None


def holds(
    stored: Mapping[str, Any],
    proposal: Proposal,
    facts: Mapping[str, TrackFacts],
    choices: MixChoices,
) -> bool:
    """``stored`` is ``applied(proposal, facts, choices)`` in the stored form (module docstring)."""
    try:
        derived = MixSpec.model_validate(applied(proposal, facts, choices))
    except (ValueError, KeyError, TypeError, IndexError):  # a refusal or a ValidationError too
        return False
    return derived.model_dump(mode="json") == dict(stored)
