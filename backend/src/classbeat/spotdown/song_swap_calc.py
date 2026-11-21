"""Spares and replacement rounds of a Spotify song pick's match stage (ADR 0036 section 3 rules,
ADR 0040 section 2 step 3; SD-P6 stage A, #1303). Pure: no I/O.

- **A candidate** (``Candidate``) is a pool pick with its match's ``use``
  (``song_match_policy``): a ``missing`` candidate is not used; a ``timing`` one is **usable**.
- **Spares first** (``short``): the usable candidates are the spares. While they can still fill
  the list (``slots`` songs, and one per slot of every role the plan needs: ``need`` is
  ``PickPlan.required_roles``, two per slot), a missing candidate is simply left out: no model
  call. Only when they cannot does a replacement round ask the song agent, at most
  ``song_match_policy.MAX_ROUNDS`` rounds, for the missing candidates the list needs
  (``to_replace``: never more than the shortfall).
- **Swaps** (``pair_swaps``): each replaced original, in order, takes the first unpaired usable
  fresh candidate of its role. The record (``Swap``) names the original as it was wanted (its
  Spotify id, title and artist, which an undo needs), the reason (its match status or an
  analysis reason) and the round.
- **The ranked spares of a slot** (``ranked_spares``, the seam of the analysis and refinement
  stages): the usable candidates of the role that are not in the list, ``timing`` first,
  then by recording-match confidence, then in pool order.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from classbeat.spotdown.song_match_policy import Use


@dataclass(frozen=True, slots=True)
class Candidate:
    spotify_id: str = field(repr=False)
    role: str
    title: str = field(repr=False)
    artist: str = field(repr=False)
    use: Use
    status: str  # the final match status
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class Swap:
    original_id: str = field(repr=False)
    title: str = field(repr=False)
    artist: str = field(repr=False)
    reason: str  # why the original was missing (a match status or an analysis reason)
    round: int


def usable(candidate: Candidate) -> bool:
    return candidate.use != "missing"


def _lacking(found: Sequence[Candidate], need: Mapping[str, int]) -> dict[str, int]:
    return {
        role: max(1, wanted // 2) - sum(c.role == role for c in found if usable(c))
        for role, wanted in need.items()
    }


def short(found: Sequence[Candidate], slots: int, need: Mapping[str, int]) -> bool:
    """Whether the usable candidates cannot fill the list."""
    if sum(usable(c) for c in found) < slots:
        return True
    return any(left > 0 for left in _lacking(found, need).values())


def to_replace(
    missing: Sequence[Candidate], found: Sequence[Candidate], slots: int, need: Mapping[str, int]
) -> list[Candidate]:
    """The missing candidates a round replaces: first one per slot a role still lacks, then
    others in order until the usable candidates could fill ``slots``."""
    lacking = _lacking(found, need)
    picked: list[Candidate] = []
    for c in missing:
        if lacking.get(c.role, 0) > 0:
            picked.append(c)
            lacking[c.role] -= 1
    rest = slots - sum(usable(c) for c in found) - len(picked)
    for c in missing:
        if rest <= 0:
            break
        if c not in picked:
            picked.append(c)
            rest -= 1
    return picked


def pair_swaps(
    originals: Sequence[Candidate], fresh: Sequence[Candidate], round_no: int
) -> tuple[dict[str, Swap], list[Candidate]]:
    """The swaps keyed by the replacement's Spotify id, and the originals left unpaired."""
    swaps: dict[str, Swap] = {}
    left: list[Candidate] = []
    for wanted in originals:
        pick = next(
            (f for f in fresh if f.role == wanted.role and usable(f) and f.spotify_id not in swaps),
            None,
        )
        if pick is None:
            left.append(wanted)
            continue
        swaps[pick.spotify_id] = Swap(
            wanted.spotify_id, wanted.title, wanted.artist, wanted.status, round_no
        )
    return swaps, left


def ranked_spares(
    found: Sequence[Candidate], listed: Collection[str], role: str
) -> list[Candidate]:
    """The spares of a slot of ``role``: usable, not in the list (``listed`` Spotify ids)."""
    spares = [c for c in found if usable(c) and c.role == role and c.spotify_id not in listed]
    return sorted(spares, key=lambda c: (c.use != "timing", -(c.confidence or 0.0)))  # stable
