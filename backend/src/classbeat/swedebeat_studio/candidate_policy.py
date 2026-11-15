"""Candidates of a studio pick and the swap records (ADR 0036 sections 3.1 and 3.3). Pure: no I/O.

- **A candidate** is a Spotify search hit with its final match verdict (after the evidence retry).
  It is **usable** only with SwedeBeat facts: found at ``SAME_SONG`` or more, with a reference, a
  duration and a BPM (``facts_of``). Everything else is really missing for the swaps.
- **Pairing** (``pair_swaps``): each really-missing original, in order, takes the first unpaired
  usable fresh candidate of the same role. The swap record is ``{original, replacement, reason,
  score}``: the original as it was wanted (its Spotify id kept for an undo), the replacement's
  Spotify title and artist, ``missing`` or ``unsure``, and the replacement's match score.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from classbeat.swedebeat_match.verdict_policy import Verdict
from classbeat.swedebeat_studio.song_list_model import Facts, Named, Original, StudioSong, Swap


@dataclass(frozen=True, slots=True)
class Candidate:
    """A search hit and its final verdict. The id and the cover are the backend's."""

    spotify_id: str = field(repr=False)
    title: str
    artist: str
    role: str
    cover: str | None = field(default=None, repr=False)
    verdict: Verdict = field(default_factory=lambda: Verdict("missing"))


def facts_of(found: Verdict) -> Facts | None:
    """The SwedeBeat facts of a found song, or None (unsure, missing, or without a fact)."""
    if found.status != "found" or found.score is None:
        return None
    if found.reference is None or found.duration is None or found.bpm is None:
        return None
    if found.title is None or found.artist is None:
        return None
    return Facts(
        reference=found.reference,
        title=found.title[:200],
        artist=found.artist[:200],
        duration_sec=found.duration,
        bpm=found.bpm,
        score=found.score,
    )


def usable(candidate: Candidate) -> bool:
    return facts_of(candidate.verdict) is not None


def original_of(candidate: Candidate) -> Original:
    return Original(
        spotify_id=candidate.spotify_id,
        title=candidate.title[:200],
        artist=candidate.artist[:200],
        cover=candidate.cover,
    )


def swap_of(original: Original, replacement: Candidate, reason: str) -> Swap:
    facts = facts_of(replacement.verdict)
    if facts is None:
        raise ValueError("only a usable candidate replaces a song")
    return Swap(
        original=original,
        replacement=Named(title=replacement.title[:200], artist=replacement.artist[:200]),
        reason=reason,  # pyright: ignore[reportArgumentType] - checked by the model
        score=facts.score,
    )


def pair_swaps(
    originals: Sequence[Candidate], fresh: Sequence[Candidate]
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
        reason = "unsure" if wanted.verdict.status == "unsure" else "missing"
        swaps[pick.spotify_id] = swap_of(original_of(wanted), pick, reason)
    return swaps, left


def song_of(candidate: Candidate, role: str, swap: Swap | None, *, new: bool = True) -> StudioSong:
    """A chosen usable candidate as the list stores it."""
    facts = facts_of(candidate.verdict)
    if facts is None:
        raise ValueError("only a usable candidate is chosen")
    return StudioSong(
        spotify_id=candidate.spotify_id,
        title=candidate.title[:200],
        artist=candidate.artist[:200],
        role=role,  # pyright: ignore[reportArgumentType] - the choose stage's Role
        new=new,
        cover=candidate.cover,
        gap=False,
        swedebeat=facts,
        swap=swap,
    )


def gap_of(candidate: Candidate) -> StudioSong:
    """A candidate of a suspected list: shown, never mixed."""
    return StudioSong(
        spotify_id=candidate.spotify_id,
        title=candidate.title[:200],
        artist=candidate.artist[:200],
        role=candidate.role,  # pyright: ignore[reportArgumentType]
        new=True,
        cover=candidate.cover,
        gap=True,
        swedebeat=None,
    )


def short(found: Sequence[Candidate], slots: int, need: Mapping[str, int]) -> bool:
    """Whether the usable candidates cannot fill the list: fewer than ``slots``, or fewer than one
    per slot of a role the plan needs (``need`` is ``PickPlan.required_roles``: two per slot)."""
    usable_found = [c for c in found if usable(c)]
    if len(usable_found) < slots:
        return True
    for role, wanted in need.items():
        if sum(c.role == role for c in usable_found) < max(1, wanted // 2):
            return True
    return False


def to_replace(
    missing: Sequence[Candidate], found: Sequence[Candidate], slots: int, need: Mapping[str, int]
) -> list[Candidate]:
    """The missing candidates a swap round replaces: first one per slot a role still lacks, then
    others in order until the usable candidates could fill ``slots``. Never more than the
    shortfall: a candidate the list does not need is not swapped."""
    usable_found = [c for c in found if usable(c)]
    lacking = {
        role: max(1, wanted // 2) - sum(c.role == role for c in usable_found)
        for role, wanted in need.items()
    }
    picked: list[Candidate] = []
    for c in missing:
        if lacking.get(c.role, 0) > 0:
            picked.append(c)
            lacking[c.role] -= 1
    rest = slots - len(usable_found) - len(picked)
    for c in missing:
        if rest <= 0:
            break
        if c not in picked:
            picked.append(c)
            rest -= 1
    return picked
