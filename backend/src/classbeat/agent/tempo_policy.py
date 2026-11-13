"""Tempo clashes between adjacent songs at the crossfade (ADR 0008 section 3, note of
2026-11-13; issue #1040).

MOWL crossfades one song into the next for ``CROSSFADE_SEC`` seconds. Two songs of very different
tempo under that overlap sound like a clash. This module classifies the BPM relation of each
adjacent pair and lists the jumps; ``choose_songs.check_song_list`` shows them to the model as a
**soft** note beside a valid answer. A jump is never an error: it does not fail a list, and so it
never uses a correction (the stage's three corrections count errors only), and ``submit_song_list``
accepts the list as it stands. Some jumps are meant, for example from the warm-up into the work or
into the cooldown, so the model may keep them.

Only numbers the model already has reach it: the BPM of two songs from ``get_pool``, their
positions and their roles (a fixed set), and one fixed sentence. Never an id or free text.

Pure: no I/O. The song stage applies to cycling only (ADR 0018: running has no song picking), so
the guidance is the cycling song stage's alone.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

TEMPO_TOLERANCE: Final = 0.06  # relative to the slower of the two, inclusive
MAX_JUMPS_SHOWN: Final = 8
_EPSILON: Final = 1e-9

Relation = Literal["compatible", "half_double", "jump"]

TEMPO_GUIDANCE: Final = (
    "Songs are crossfaded, so prefer adjacent songs of a compatible tempo (within about 6 %, or "
    "half or double time) and jump to a very different tempo only where the class changes phase, "
    "for example from the warmup into the work or into the cooldown. check_song_list lists any "
    "tempo jumps it finds as tempo_jumps; they are a hint, never an error, so keep one that is "
    "intended."
)
NOTE: Final = (
    "tempo_jumps: adjacent songs whose tempos differ a lot, so the crossfade may clash; keep one "
    "that is intended (a change of phase), otherwise reorder."
)


@dataclass(frozen=True, slots=True)
class TempoJump:
    position: int  # the first of the two songs; the pair is (position, position + 1)
    from_bpm: float
    to_bpm: float
    from_role: str
    to_role: str

    @property
    def phase_change(self) -> bool:
        return self.from_role != self.to_role


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TEMPO_TOLERANCE * min(a, b) + _EPSILON


def relation(a: float, b: float) -> Relation:
    """How two tempos relate: ``compatible`` (within the tolerance), ``half_double`` (one is half
    or double the other, within the tolerance) or ``jump``. A tempo that is not a positive finite
    number cannot be judged and counts as compatible."""
    if not all(math.isfinite(x) and x > 0 for x in (a, b)):
        return "compatible"
    if _close(a, b):
        return "compatible"
    if _close(a, 2 * b) or _close(2 * a, b):
        return "half_double"
    return "jump"


def tempo_jumps(bpms: Sequence[float], roles: Sequence[str]) -> tuple[TempoJump, ...]:
    """The adjacent pairs of the list whose relation is a ``jump``, in play order."""
    return tuple(
        TempoJump(n, bpms[n], bpms[n + 1], roles[n], roles[n + 1])
        for n in range(min(len(bpms), len(roles)) - 1)
        if relation(bpms[n], bpms[n + 1]) == "jump"
    )


def jumps_view(jumps: Sequence[TempoJump]) -> dict[str, Any]:
    """The soft note for ``check_song_list``'s answer, or nothing when there is no jump: per jump
    the two positions, their BPM rounded and their roles, and one fixed sentence."""
    if not jumps:
        return {}
    shown = [
        {
            "from": j.position,
            "to": j.position + 1,
            "bpm": [round(j.from_bpm), round(j.to_bpm)],
            "roles": [j.from_role, j.to_role],
        }
        for j in jumps[:MAX_JUMPS_SHOWN]
    ]
    return {"tempo_jumps": shown, "tempo_note": NOTE}
