"""The soft checks of a mix spec's transitions (#1330; ADR 0036 section 4.2, A7 step 10). Pure:
no I/O.

``hints`` runs on a spec that ``mix_plan_policy.checked`` passed (cuts placed, every rule held) and
never refuses: each hint reaches the model as a named warning it can fix. A hint names **piece
indexes** and numbers the model already has (BPM, whole seconds), never a reference. For each
transition in mix order, ``tempo_jump`` comes before ``downbeats_not_locked``.

- ``tempo_jump``: the two neighbours' tempos are neither close nor half or double time. This is
  the song stage's tempo check (``agent.tempo_policy``, #1040) on the pieces in mix order, not a
  second one. No tempo is ever changed: the model reorders the approved tracks so neighbours fit.
- ``downbeats_not_locked`` (``locked``): during the overlap of a crossfade the two tracks'
  downbeats do not meet within ``LOCK_TOLERANCE_S`` (30 ms). It applies only where both tracks are
  ``exact`` and have **trusted** downbeats (#1328's candidates, ``mix_cut_policy.trusted``); any
  other transition, and a crossfade of 0 (no overlap), stays as today and is never flagged. On the
  overlap's clock (0 where the incoming piece starts, the crossfade where the outgoing one ends)
  every downbeat of each side, from 30 ms before to 30 ms after the overlap, needs one of the
  other side within the tolerance. For tempos in half or double time only the slower track's
  downbeats need a partner (the faster one has a downbeat in the middle of each slow bar). The
  points compared are the placed cuts against spotdown's own downbeat times, so a grid that drifts
  shows here.
- **A whole number of bars.** The crossfade is whole seconds (the spec's shape stays, ADR 0036
  A1), so it can hold whole bars only for some tempos (120 BPM: every even second; 128 BPM: 15 s
  and 30 s; 125 BPM: none from 1 to 30 s within 30 ms). The lock hint names up to
  ``MAX_SUGGESTED`` crossfades that would lock with the same cuts, the nearest first, within the
  spec's crossfade rules; with none, it says so.
"""

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from classbeat.agent.tempo_policy import relation, tempo_jumps
from classbeat.studio.mix_cut_policy import trusted
from classbeat.studio.mix_facts_model import CS, TrackFacts, cs
from classbeat.studio.mix_plan_policy import MAX_CROSSFADE

LOCK_TOLERANCE_S: Final = 0.03
MAX_SUGGESTED: Final = 2
TEMPO_JUMP: Final = "tempo_jump"
NOT_LOCKED: Final = "downbeats_not_locked"
_EPSILON: Final = 1e-9


@dataclass(frozen=True, slots=True)
class TransitionHint:
    code: str
    pieces: tuple[int, int]
    bpm: tuple[float, ...] = ()  # tempo_jump only: the two tempos
    crossfades: tuple[int, ...] = ()  # downbeats_not_locked: crossfades that would lock

    def text(self) -> str:
        """The hint for the model: its code, the piece indexes and numbers only."""
        a, b = self.pieces
        if self.code == TEMPO_JUMP:
            return (
                f"{TEMPO_JUMP}: pieces {a} and {b} are at {self.bpm[0]:g} and {self.bpm[1]:g} BPM, "
                "neither close nor half or double time, so their crossfade may clash. Reorder the "
                "tracks so neighbours fit; keep it only where the class changes phase."
            )
        head = (
            f"{NOT_LOCKED}: in the crossfade between pieces {a} and {b} the two tracks' downbeats "
            "do not meet within 30 ms."
        )
        if self.crossfades:
            named = " or ".join(f"{x} s" for x in self.crossfades)
            return f"{head} A crossfade of {named} (whole bars) would lock them with these cuts."
        return (
            f"{head} No crossfade from 1 to {MAX_CROSSFADE} s locks them with these cuts: cut on a "
            "downbeat or phrase point, use a crossfade of 0, or reorder."
        )


def downbeats(facts: TrackFacts) -> tuple[float, ...]:
    """The trusted downbeats of an ``exact`` track, in time order (none for any other)."""
    if facts.timing_class != "exact":
        return ()
    return tuple(
        sorted(c.at_s for c in facts.candidates if c.kind == "downbeat" and trusted(c, facts))
    )


def _met(points: Sequence[float], others: Sequence[float]) -> bool:
    """Whether every point has one of ``others`` (sorted) within the tolerance."""
    for p in points:
        i = bisect_left(others, p - LOCK_TOLERANCE_S - _EPSILON)
        if i == len(others) or others[i] > p + LOCK_TOLERANCE_S + _EPSILON:
            return False
    return True


def locked(
    outgoing: TrackFacts, end: float, incoming: TrackFacts, start: float, crossfade: int
) -> bool | None:
    """Whether the downbeats lock in the overlap of ``crossfade`` seconds between the outgoing
    piece's ``end`` and the incoming piece's ``start``; ``None`` where the check does not apply
    (no overlap, or a side that is not ``exact`` with trusted downbeats)."""
    out_beats, in_beats = downbeats(outgoing), downbeats(incoming)
    if crossfade <= 0 or not out_beats or not in_beats:
        return None
    base = cs(end) / CS - crossfade  # the outgoing track's time where the overlap starts
    first = cs(start) / CS
    a = [d - base for d in out_beats]
    b = [d - first for d in in_beats]

    def inside(points: Sequence[float]) -> list[float]:
        return [p for p in points if -LOCK_TOLERANCE_S <= p <= crossfade + LOCK_TOLERANCE_S]

    if relation(outgoing.bpm, incoming.bpm) == "half_double":
        slow, fast = (a, b) if outgoing.bpm < incoming.bpm else (b, a)
        return _met(inside(slow), fast)
    return _met(inside(a), b) and _met(inside(b), a)


def locking_crossfades(
    outgoing: TrackFacts, end: float, incoming: TrackFacts, start: float, now: int, limit: int
) -> tuple[int, ...]:
    """Up to ``MAX_SUGGESTED`` crossfades from 1 to ``limit`` s that lock, nearest ``now`` first."""
    found = [x for x in range(1, limit + 1) if locked(outgoing, end, incoming, start, x)]
    return tuple(sorted(found, key=lambda x: (abs(x - now), x))[:MAX_SUGGESTED])


def _limit(windows: Sequence[int], crossfades: Sequence[int], n: int) -> int:
    """The longest crossfade ``n`` the spec's rules allow (whole seconds, in hundredths below)."""
    room = min(windows[n], windows[n + 1])
    if n > 0:
        room = min(room, windows[n] - CS * crossfades[n - 1])
    if n + 1 < len(crossfades):
        room = min(room, windows[n + 1] - CS * crossfades[n + 1])
    return max(0, min(MAX_CROSSFADE, (room - 1) // CS))


def hints(spec: Mapping[str, Any], facts: Mapping[str, TrackFacts]) -> list[TransitionHint]:
    """The transitions' soft checks of a checked spec; ``facts`` by reference."""
    tracks = spec["tracks"]
    crossfades = [int(c) for c in spec["crossfades"]]
    known = [facts[t["reference"]] for t in tracks]
    bpms = [f.bpm for f in known]
    jumps = {j.position for j in tempo_jumps(bpms, [""] * len(bpms))}
    windows = [cs(t["end_sec"]) - cs(t["start_sec"]) for t in tracks]
    out: list[TransitionHint] = []
    for n, fade in enumerate(crossfades):
        if n in jumps:
            tempos = (round(bpms[n], 1), round(bpms[n + 1], 1))
            out.append(TransitionHint(TEMPO_JUMP, (n, n + 1), tempos))
        end, start = float(tracks[n]["end_sec"]), float(tracks[n + 1]["start_sec"])
        if locked(known[n], end, known[n + 1], start, fade) is False:
            limit = _limit(windows, crossfades, n)
            better = locking_crossfades(known[n], end, known[n + 1], start, fade, limit)
            out.append(TransitionHint(NOT_LOCKED, (n, n + 1), crossfades=better))
    return out


def texts(found: Sequence[TransitionHint]) -> list[str]:
    return [h.text() for h in found]
