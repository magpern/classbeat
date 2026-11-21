"""The workout moved onto a derived mix timeline by song anchor (ADR 0036 amendment B4a, #1367).
Pure: no I/O.

``remapped(proposal, derived, secs)`` takes the proposal's **checked** spec, the derived spec as
``mix_plan_policy.checked`` returned it and the proposal workout's interval lengths (``sec`` of
each interval in order, whole seconds), and returns the new lengths (``boundaries`` is steps 1
to 4):

1. each interval boundary (the running sum of ``secs``) is a mix time ``t`` on the proposal's
   composed map; it belongs to the **last piece that has started** at ``t`` (the incoming piece in
   an overlap) and so to a source time ``s`` of that piece's track;
2. a kept piece moves it to the derived mix time where that piece plays ``s``, with ``s``
   **clamped to the piece's derived own part** (its window less its outgoing crossfade), so the
   map is monotone whatever ``checked`` placed;
3. a dropped piece moves it to the derived start of the next kept piece, or to the **derived
   composed end** when no kept piece follows;
4. the start stays at 0; the end goes to the derived composed end when it was within
   ``END_SLACK_CS`` of the proposal's composed end, else it maps like any boundary;
5. each boundary is rounded to a whole second (half up, in whole hundredths) and the lengths are
   the differences of the rounded boundaries, so the total is the rounded end; a length under
   ``mowl.sync.MIN_INTERVAL`` is ``workout_does_not_fit`` (never squeezed).

Kept and dropped pieces are matched in mix order (``kept``): ``applied`` keeps every piece of a
trimmed song and only the first piece of a whole one, so the derived references are a subsequence
of the proposal's and the earliest match is that derivation. Anything else is a ``ValueError``.
All times are whole hundredths (``cs``), as ``mowl.compose`` lays a mix out.
"""

import itertools
from collections.abc import Mapping, Sequence
from typing import Any, Final

from mowl.sync import MIN_INTERVAL

from classbeat.studio.mix_choice_model import MixChoiceRefused
from classbeat.studio.mix_facts_model import CS, cs

DOES_NOT_FIT: Final = "workout_does_not_fit"
END_SLACK_CS: Final = CS  # 1 s


class _Layout:
    """One checked spec laid out: per piece its window and mix start, and the composed end."""

    def __init__(self, spec: Mapping[str, Any]) -> None:
        tracks = spec["tracks"]
        fades = [int(c) * CS for c in spec["crossfades"]]
        if len(fades) != len(tracks) - 1:
            raise ValueError("one crossfade per change of piece")
        self.references = [str(t["reference"]) for t in tracks]
        self.windows = [(cs(t["start_sec"]), cs(t["end_sec"])) for t in tracks]
        self.fade_out = [*fades, 0]
        self.starts: list[int] = []
        at = 0
        for (a, b), fade in zip(self.windows, self.fade_out, strict=True):
            self.starts.append(at)
            at += b - a - fade
        self.end = at

    def playing(self, t: int) -> int:
        """The last piece that has started by mix time ``t``."""
        return max(n for n, start in enumerate(self.starts) if start <= t)


def kept(proposal: Sequence[str], derived: Sequence[str]) -> list[int | None]:
    """Per proposal piece, the derived piece it became, ``None`` when dropped."""
    out: list[int | None] = []
    j = 0
    for reference in proposal:
        if j < len(derived) and derived[j] == reference:
            out.append(j)
            j += 1
        else:
            out.append(None)
    if j != len(derived):
        raise ValueError("the derived spec is not a derivation of the proposal")
    return out


def _moved(t: int, old: _Layout, new: _Layout, match: Sequence[int | None]) -> int:
    k = old.playing(t)
    j = match[k]
    if j is None:
        later = [m for m in match[k + 1 :] if m is not None]
        return new.starts[later[0]] if later else new.end
    source = old.windows[k][0] + t - old.starts[k]
    start, end = new.windows[j]
    source = min(max(source, start), end - new.fade_out[j])
    return new.starts[j] + source - start


def _whole_second(at: int) -> int:
    return (at + CS // 2) // CS


def boundaries(
    proposal: Mapping[str, Any], derived: Mapping[str, Any], secs: Sequence[int]
) -> list[int]:
    """Steps 1 to 4: each interval's end (the workout's end last) on the derived timeline, in
    whole hundredths, before the rounding; the start, 0, is not listed."""
    old, new = _Layout(proposal), _Layout(derived)
    match = kept(old.references, new.references)
    ends: list[int] = []
    at = 0
    for sec in secs:
        at += int(sec) * CS
        ends.append(at)
    moved = [_moved(t, old, new, match) for t in ends[:-1]]
    if ends:
        last = ends[-1]
        near = abs(last - old.end) <= END_SLACK_CS
        moved.append(new.end if near else _moved(last, old, new, match))
    return moved


def remapped(
    proposal: Mapping[str, Any], derived: Mapping[str, Any], secs: Sequence[int]
) -> list[int]:
    """The interval lengths on the derived timeline (module docstring, step 5), else
    ``MixChoiceRefused("workout_does_not_fit")``."""
    rounded = [0, *(_whole_second(b) for b in boundaries(proposal, derived, secs))]
    lengths = [b - a for a, b in itertools.pairwise(rounded)]
    short = [n for n, sec in enumerate(lengths) if sec < MIN_INTERVAL]
    if short:
        raise MixChoiceRefused(
            DOES_NOT_FIT,
            f"interval {short[0]} would be {lengths[short[0]]} s on the new timeline, under "
            f"{MIN_INTERVAL} s; re-plan the class",
        )
    return lengths
