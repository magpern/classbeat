"""What the mix screen suggests per approved song (ADR 0036 amendment B, B3 and B5, #1367). Pure:
no I/O.

``suggestions(proposal, facts)``, per approved song in list order: its analysed length, its stored
timing class, the proposal's pieces of it (``(start, end)`` in mix order), whether each proposed
end lands within ``LOCK_TOLERANCE_MS`` of a trusted phrase point (A4.2; display only, it decides
nothing), and ``full_only``, the reason a song whose stored timing is not ``exact`` is never trimmed
(``timing_approximate``, ``timing_unusable``; ``mix_choice_policy.applied``
keeps it whole whatever the proposal says).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown.timing_inputs_model import TimingClass
from classbeat.studio.mix_choice_model import Proposal, approved_facts, pieces
from classbeat.studio.mix_cut_policy import trusted
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_loop_policy import LOCK_TOLERANCE_MS, PHRASE_KINDS

FULL_ONLY: Final[dict[TimingClass, str]] = {
    "approximate": "timing_approximate",
    "unusable": "timing_unusable",
}


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One approved song on the screen: ``pieces`` the proposal's ``(start, end)`` in mix order,
    ``ends_on_phrase`` per piece (display only), ``full_only`` the reason it is never trimmed."""

    position: int
    length: float
    timing_class: TimingClass
    pieces: tuple[tuple[float, float], ...]
    ends_on_phrase: tuple[bool, ...]
    full_only: str | None


def on_phrase(at: float, known: TrackFacts) -> bool:
    """Within ``LOCK_TOLERANCE_MS`` of a trusted phrase point (A4.2), in whole milliseconds."""
    ms = round(at * 1000)
    return any(
        trusted(c, known)
        and c.kind in PHRASE_KINDS
        and abs(ms - round(c.at_s * 1000)) <= LOCK_TOLERANCE_MS
        for c in known.candidates
    )


def suggestions(proposal: Proposal, facts: Mapping[str, TrackFacts]) -> list[Suggestion]:
    played = pieces(proposal.spec)
    out: list[Suggestion] = []
    for position, (reference, known) in enumerate(approved_facts(proposal, facts)):
        own = tuple((a, b) for r, a, b in played if r == reference)
        out.append(
            Suggestion(
                position,
                known.length,
                known.timing_class,
                own,
                tuple(on_phrase(b, known) for _, b in own),
                None if known.timing_class == "exact" else FULL_ONLY[known.timing_class],
            )
        )
    return out
