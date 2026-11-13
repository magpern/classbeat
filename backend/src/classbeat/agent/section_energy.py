"""The energy of a music section as the model sees it (ADR 0010, 2026-11-13 note).

mowl-workouts' Spotify map gives each section an ``energy`` from 0 (the quietest part of its own
track) to 1 (the loudest), ranked by Spotify's loudness within the track; it says nothing across
tracks and holds no audio. This module turns that number into what a view may carry: a plain
float clamped to 0..1 and rounded to 2 decimals, or nothing. A map analysed before section energy,
a one-section track and a flat-sounding track have none, and a Swedebeat mix never does.

Pure: no I/O. ``model_input.track_detail`` shows one number per section; ``songs.analysed_pool``
folds a pool track's sections into at most ``MAX_SECTIONS`` spans (``pool_sections``). The two
guidance lines are the stage prompts' wording of how to use it.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from mowl import spotify

MAX_SECTIONS: Final = 8  # spans shown per pool track
# A section shorter than the first step is folded into its neighbour (mowl's own rule); a track
# with more than ``MAX_SECTIONS`` spans is folded again with the next, longer minimum.
FOLD_STEPS: Final = (13, 20, 30, 45, 60, 90, 120)

DESIGN_GUIDANCE: Final = (
    "Where a track's sections show an energy (0 is the quietest part of that track, 1 the "
    "loudest; it compares a track's own sections, never one song with another), put the "
    "warm-up, the recoveries and the cooldown on its quiet sections and the hard work "
    "intervals on its loud ones."
)
SONGS_GUIDANCE: Final = (
    "A pool track's sections are [from_sec, to_sec, energy] spans, energy from 0 (the quietest "
    "part of that track) to 1 (the loudest), compared within the track only. Whole songs play: "
    "prefer songs that stay quiet for the warmup, recovery and cooldown roles and songs with "
    "long loud sections for the hard work."
)


def energy_of(section: Mapping[str, Any]) -> float | None:
    """The section's energy clamped to 0..1 and rounded, or ``None`` when it has none."""
    value = section.get("energy")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return round(min(1.0, max(0.0, float(value))), 2)


def pool_sections(
    sections: Sequence[Mapping[str, Any]], start: float, length_sec: int
) -> tuple[tuple[int, int, float], ...]:
    """One track's ``(from_sec, to_sec, energy)`` spans, seconds from the track's start: the
    map's sections of that track, short ones folded into a neighbour (``spotify.energy_sections``),
    folded further until at most ``MAX_SECTIONS`` remain. Sections without energy are left out."""
    folded: list[dict[str, Any]] = []
    for min_sec in FOLD_STEPS:
        folded = spotify.energy_sections({"songs": list(sections)}, min_sec)
        if len(folded) <= MAX_SECTIONS:
            break
    out: list[tuple[int, int, float]] = []
    for span in folded[:MAX_SECTIONS]:
        energy = energy_of(span)
        begin = min(length_sec, max(0, round(float(span["start"]) - start)))
        end = min(length_sec, max(0, round(float(span["end"]) - start)))
        if energy is not None and end > begin:
            out.append((begin, end, energy))
    return tuple(out)
