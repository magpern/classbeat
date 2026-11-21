"""B9's two facts of a studio draft chosen for SwedeBeat outputs (ADR 0036 amendment B9, #1370).
Pure: no I/O. ``swedebeat_studio.applied_form`` reads what they take.

- ``mixed``: not ``mix_choice_policy.is_plain`` of the draft's stored, hash-checked spec, the
  lengths from the analyses the draft holds. A reference without one is mixed (fail closed).
- ``proposal_of``: B4's proposal on a draft's lineage (``Link``, the draft itself first, then each
  parent): the nearest **draft** on the same ``song_list`` whose provenance ``origin`` is in the
  **allowlist** ``PROPOSAL_ORIGINS`` (the studio design and revise jobs' model designs). Every other
  origin (``mix_choices``, ``duplicate``, ``beat_one``, none, any origin added later until this list
  names it) is passed over, so ``applied`` never runs on a derived spec.
- ``basis``: the proposal and the choices the draft must be ``applied`` of. A proposal itself
  (its origin in the allowlist) with the choices the chosen outputs imply (``implied``: whole
  songs without ``swedebeat_mix``, the DJ-mix defaults with it); an applied draft
  (``origin: mix_choices``, #1369) with its recorded ``proposal_revision_id``, which must be the
  proposal ``proposal_of`` resolves, and its recorded ``choices``. Any other draft, or a record
  that does not read, has none (``None``).
- ``holds``: the stored spec equals ``applied(proposal, facts, choices)`` in the stored form.
  An ``applied`` that refuses, or anything that does not read, does not hold (fail closed).

Whatever choices are recorded, a spec equal to their ``applied`` keeps B5 and B6 (``applied``
enforces both), so the record can never widen what is admitted.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from classbeat.studio.mix_choice_model import MixChoices, Proposal, parsed
from classbeat.studio.mix_choice_policy import applied, is_plain
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_spec_model import MixSpec

# B4's allowlist of model designs, as ``swedebeat_studio.draft.build`` stores them.
PROPOSAL_ORIGINS: Final = frozenset({"design_draft", "revise_draft"})
APPLIED_ORIGIN: Final = "mix_choices"  # the ``apply_mix_choices`` job's draft (#1369)


@dataclass(frozen=True, slots=True)
class Link:
    """One revision of a draft's lineage as the lookup reads it."""

    revision_id: UUID
    kind: str
    song_list_id: str | None
    origin: str | None


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


def proposal_of(chain: Sequence[Link], song_list_id: str) -> UUID | None:
    """B4's proposal: the first allowlisted draft of ``chain`` on ``song_list_id``."""
    for link in chain:
        if (
            link.kind == "draft"
            and link.song_list_id == song_list_id
            and link.origin in PROPOSAL_ORIGINS
        ):
            return link.revision_id
    return None


def _recorded(provenance: Mapping[str, Any]) -> tuple[UUID, MixChoices] | None:
    raw = provenance.get("proposal_revision_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw), parsed(provenance.get("choices"))
    except ValueError:  # an id or choices that do not read (``MixChoiceRefused`` included)
        return None


def basis(
    chain: Sequence[Link],
    song_list_id: str,
    provenance: Mapping[str, Any],
    outputs: Collection[str],
) -> Basis | None:
    """The proposal and choices ``chain[0]`` (the draft) must be ``applied`` of, or None."""
    if not chain:
        return None
    found = proposal_of(chain, song_list_id)
    if found is None:
        return None
    draft = chain[0]
    if draft.origin in PROPOSAL_ORIGINS:
        return Basis(found, implied(outputs)) if found == draft.revision_id else None
    if draft.origin == APPLIED_ORIGIN:
        recorded = _recorded(provenance)
        if recorded is None or recorded[0] != found:
            return None  # not the proposal B4 resolves for this draft (any more)
        return Basis(found, recorded[1])
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
