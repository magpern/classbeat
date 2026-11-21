"""Which transition curve the rendered mix takes (#1330; ADR 0036 section 6, Mix). Pure: no I/O.

The mix spec has no curve (its shape stays, ADR 0036 A1): the backend chooses one per transition
for the **mix output only** (``output_body_policy.render_plan``), from the stored analyses, never
from model text. The SwedeBeat playlist output carries no curve (the app applies only cuts and
crossfades). ``equal_power`` is the default and today's render. ``bass_swaps`` gives a transition
``bass_swap`` only when all of these hold:

- the crossfade overlaps (more than 0 s) and its downbeats **lock** (``mix_transition_policy.
  locked`` is true: both tracks ``exact`` with trusted downbeats, met within 30 ms);
- **both** sides have real bass in the overlap: each track's stored low-band curve
  (``spotdown.low_band_model``, an ``exact`` track's only) has a mean of at least ``BASS_MIN``
  over its part of the overlap. Where either side shows none, or the curve is not known, the
  curve stays ``equal_power``;
- the incoming track has a trusted downbeat inside the overlap, at least the render's handover
  (``render_policy.SWAP_RAMP_FRAMES``) after its start and before its end: the swap point is the
  one nearest the overlap's middle (a tie goes to the earlier).

The swap point is given in samples into the incoming track's fade-in, as the render plan counts.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.spotdown.low_band_model import mean
from classbeat.studio.mix_facts_model import CS, TrackFacts, cs
from classbeat.studio.mix_transition_policy import downbeats, locked
from classbeat.studio.render_policy import RATE, SWAP_RAMP_FRAMES

BASS_MIN: Final = 0.3  # of the track's own usual bass (spotdown normalises the low band so)


def _bass(facts: TrackFacts, start: float, end: float) -> bool:
    level = None if facts.low_band is None else mean(facts.low_band, start, end)
    return level is not None and level >= BASS_MIN


def swap_frames(
    outgoing: TrackFacts, end: float, incoming: TrackFacts, start: float, crossfade: int
) -> int | None:
    """The bass swap's point in samples into the overlap, or ``None`` for ``equal_power``."""
    if locked(outgoing, end, incoming, start, crossfade) is not True:
        return None
    end, start = cs(end) / CS, cs(start) / CS
    if not _bass(outgoing, end - crossfade, end) or not _bass(incoming, start, start + crossfade):
        return None
    fade = crossfade * RATE
    points = [round((d - start) * RATE) for d in downbeats(incoming)]
    inside = [p for p in points if SWAP_RAMP_FRAMES <= p < fade]
    if not inside:
        return None
    return min(inside, key=lambda p: (abs(2 * p - fade), p))


def bass_swaps(spec: Mapping[str, Any], facts: Mapping[str, TrackFacts]) -> Sequence[int | None]:
    """Per transition in mix order, its swap point, or ``None`` where it stays ``equal_power``."""
    tracks = spec["tracks"]
    return [
        swap_frames(
            facts[tracks[n]["reference"]],
            float(tracks[n]["end_sec"]),
            facts[tracks[n + 1]["reference"]],
            float(tracks[n + 1]["start_sec"]),
            int(fade),
        )
        for n, fade in enumerate(spec["crossfades"])
    ]
