"""The re-plan section of a studio revise's first message (ADR 0036 amendment B6, #1372). Pure: no
I/O.

``choices_section(choices)`` states the user's ``MixChoices`` to the model **first**: the form from
``dj_mix``, ``blend`` and ``trim`` (DJ mix off: whole songs, no crossfades, no trims; Blend off:
hard cuts only, no crossfades; Trim off: every song once, whole), then, **by list index** (an
approved list position is the stage's track index: the studio list has no gap), the songs kept
whole and the hard cuts, and asks for a re-plan of the rest. Only booleans and whole numbers
reach the text: never a reference, a title or anything the user typed. The result is an ordinary
revise (a new proposal); ``mix_choice_policy.applied`` applies the same choices to it afterwards
and is authoritative, so the text is guidance, never a guarantee.
"""

from collections.abc import Sequence
from typing import Final

from classbeat.studio.mix_choice_model import MixChoices

HEAD: Final = "The instructor's mix choices for this re-plan (tracks by index):\n"
WHOLE_SONGS: Final = (
    "whole songs, no crossfades, no trims: every track plays once, whole (start_sec 0, end_sec its "
    "length), and every crossfade is 0"
)
WHOLE_HARD: Final = (
    "DJ mix, every song once, whole, with hard cuts only: no trims, no repeats and no crossfades "
    "(every crossfade 0)"
)
WHOLE_BLEND: Final = (
    "DJ mix, every song once, whole: no trims and no repeats (start_sec 0, end_sec its length); "
    "crossfades allowed"
)
HARD_CUTS: Final = (
    "DJ mix with hard cuts only: no crossfades (every crossfade 0); songs may be trimmed"
)
FULL_MIX: Final = "DJ mix: songs may be trimmed and blended with crossfades"
TAIL: Final = (
    "Re-plan everything else (the other tracks' cuts, the order, the crossfades the form allows "
    "and the workout) so the class fits the brief's length. Your result is a new proposal: these "
    "choices are applied to it afterwards, and whatever breaks one is undone.\n\n"
)


def form(choices: MixChoices) -> str:
    if not choices.dj_mix:
        return WHOLE_SONGS
    if not choices.trim:
        return WHOLE_BLEND if choices.blend else WHOLE_HARD
    return FULL_MIX if choices.blend else HARD_CUTS


def _tracks(positions: Sequence[int]) -> str:
    return "tracks " + ", ".join(str(int(p)) for p in positions) if positions else "none"


def choices_section(choices: MixChoices) -> str:
    lines = [f"- form: {form(choices)}"]
    if choices.dj_mix:
        lines += [
            f"- keep whole (once, from 0 to its length): {_tracks(choices.keep_whole)}",
            "- hard cut in (crossfade 0 on the change into each of its pieces): "
            + _tracks(choices.hard_cut_in),
            "- hard cut out (crossfade 0 on the change out of each of its pieces): "
            + _tracks(choices.hard_cut_out),
        ]
    return HEAD + "\n".join(lines) + "\n" + TAIL
