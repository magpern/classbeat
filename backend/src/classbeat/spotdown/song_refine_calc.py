"""The suitability refinement of a chosen Spotify song list (ADR 0040 section 2 step 5, plan
section 1 step 5; SD-P6 stage B, #1303): which spare replaces an unsuitable song. Pure: no I/O.

``track_fit_policy.plan_round`` decides which positions a round replaces (at most a third of the
list, worst first, at most ``MAX_ROUNDS`` rounds). Each is replaced **alone** by ``spare_for``:
the first of the slot's ranked spares (``MatchedPool.spares``: the usable candidates of its role
not in the list, ``timing`` first) that MOWL analysed, that was never in the list (``tried``: a
song replaced or tried once is never a spare again), that is not the same song as another one of
the list (``key_of``), and with which the list's total still lies within the tolerance of the
target (``within_tolerance``). No model call: when no spare qualifies the song is accepted with a
warning.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from classbeat.agent.songs import key_of, total_sec, within_tolerance


@dataclass(frozen=True, slots=True)
class Slot:
    """A song of the list, or a spare, as the choice needs it."""

    spotify_id: str
    title: str
    artist: str
    length_sec: int


def spare_for(
    position: int,
    listed: Sequence[Slot],
    spares: Sequence[Slot],
    tried: Collection[str],
    target_sec: int,
) -> int | None:
    """The index in ``spares`` of the one that replaces ``listed[position]``, or ``None``."""
    others = [s for n, s in enumerate(listed) if n != position]
    taken = {key_of(s.title, s.artist) for s in others}
    ids = {s.spotify_id for s in listed}
    for n, spare in enumerate(spares):
        if spare.spotify_id in ids or spare.spotify_id in tried:
            continue
        if key_of(spare.title, spare.artist) in taken:
            continue
        lengths = [s.length_sec for s in others] + [spare.length_sec]
        if within_tolerance(total_sec(lengths), target_sec):
            return n
    return None
