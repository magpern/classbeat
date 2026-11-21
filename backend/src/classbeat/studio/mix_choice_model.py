"""The user's mix choices on a studio draft and the proposal they apply to (ADR 0036 amendment B,
B2 to B4, #1367). Pure: no I/O.

``MixChoices`` is ``dj_mix``, ``blend``, ``trim`` and three sets of **approved-list positions**
(never references, section 3.4): ``keep_whole``, ``hard_cut_in`` and ``hard_cut_out``. With
``dj_mix`` false the choices are the **whole-songs form**: ``blend`` and ``trim`` false and the
three sets empty, else ``invalid_input``. A set is stored sorted and never holds a position twice;
``canonical`` is its one JSON form (keys sorted, no space, no unknown field). Whether a position is
on the list is checked against the list (``positions_refusal``).

``Proposal`` is what ``mix_choice_policy.applied`` derives from: the approved list's references by
position (``None`` for a gap), the proposal draft's stored, checked mix spec and the list's
``target_sec``. ``MixChoiceRefused`` carries one named reason; for
``class_length_out_of_tolerance`` also the composed total and the allowed range (B6).
``pieces`` reads a spec's ``(reference, start, end)`` and ``approved_facts`` the approved songs'
stored facts, for the policies that share them.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_plan_policy import COVERAGE

INVALID_INPUT: Final = "invalid_input"
Positions = tuple[Annotated[int, Field(ge=0)], ...]


class MixChoiceRefused(ValueError):
    def __init__(
        self,
        reason: str,
        detail: str,
        total_sec: float | None = None,
        allowed: tuple[float, float] | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.total_sec = total_sec
        self.allowed = allowed


class MixChoices(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dj_mix: bool
    blend: bool
    trim: bool
    keep_whole: Positions = ()
    hard_cut_in: Positions = ()
    hard_cut_out: Positions = ()

    @model_validator(mode="after")
    def _sets_and_form(self) -> Self:
        for found in (self.keep_whole, self.hard_cut_in, self.hard_cut_out):
            if list(found) != sorted(set(found)):
                raise ValueError("a set of positions is sorted, each position once")
        if not self.dj_mix and (
            self.blend or self.trim or self.keep_whole or self.hard_cut_in or self.hard_cut_out
        ):
            raise ValueError("whole songs: blend and trim off, no per-song choice")
        return self

    @classmethod
    def whole_songs(cls) -> Self:
        return cls(dj_mix=False, blend=False, trim=False)

    @classmethod
    def dj_defaults(cls) -> Self:
        """DJ mix with Blend and Trim on and no per-song choice (B2, B9)."""
        return cls(dj_mix=True, blend=True, trim=True)


def parsed(raw: object) -> MixChoices:
    """``raw`` (a JSON object, sets as arrays in any order) as ``MixChoices``, else
    ``invalid_input``."""
    if not isinstance(raw, Mapping):
        raise MixChoiceRefused(INVALID_INPUT, "the choices are an object")
    item: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
    for key in ("keep_whole", "hard_cut_in", "hard_cut_out"):
        value = item.get(key, [])
        if not isinstance(value, list):
            raise MixChoiceRefused(INVALID_INPUT, f"{key} is a list of positions")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):  # pyright: ignore[reportUnknownVariableType]
            raise MixChoiceRefused(INVALID_INPUT, f"{key} is a list of positions")
        item[key] = tuple(sorted(value))  # pyright: ignore[reportUnknownArgumentType]
    try:
        return MixChoices.model_validate(item)
    except ValidationError as exc:
        raise MixChoiceRefused(INVALID_INPUT, "the choices do not read") from exc


def canonical(choices: MixChoices) -> str:
    return json.dumps(choices.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def positions_refusal(choices: MixChoices, count: int) -> None:
    """``invalid_input`` when a chosen position is not on a list of ``count`` songs."""
    for found in (choices.keep_whole, choices.hard_cut_in, choices.hard_cut_out):
        if any(p >= count for p in found):
            raise MixChoiceRefused(INVALID_INPUT, "a chosen position is not on the list")


@dataclass(frozen=True, slots=True)
class Proposal:
    """The proposal ``applied`` derives from (B4): never an applied draft."""

    approved: Sequence[str | None]
    spec: Mapping[str, Any]
    target_sec: int


def pieces(spec: Mapping[str, Any]) -> list[tuple[str, float, float]]:
    return [
        (str(t["reference"]), float(t["start_sec"]), float(t["end_sec"])) for t in spec["tracks"]
    ]


def approved_facts(
    proposal: Proposal, facts: Mapping[str, TrackFacts]
) -> list[tuple[str, TrackFacts]]:
    """The approved references with their stored facts, in list order; a gap or a song with no
    facts is refused by ``checked``'s name for it (``list_has_gap``, ``not_a_permutation``)."""
    out: list[tuple[str, TrackFacts]] = []
    for reference in proposal.approved:
        if reference is None:
            raise MixChoiceRefused("list_has_gap", "a gap can never go into a mix")
        if reference not in facts:
            raise MixChoiceRefused("not_a_permutation", COVERAGE)
        out.append((reference, facts[reference]))
    return out
