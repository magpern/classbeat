"""The soft checks of a mix spec with repeats (ADR 0036 amendment A2 and A5, step 10 of A7; #1342).
Pure: no I/O.

``hints`` runs on a spec that ``mix_plan_policy.checked`` passed (snapped, every rule held) and
never refuses: each hint reaches the model as a named warning and is shown on the timeline. A hint
names **piece indexes** (positions in the mix), never a reference.

- ``whole_track_repeated`` (A5): one reference has two or more pieces that each cover at least
  90 % of the track's analysed length (``10 * (cs(end) - cs(start)) >= 9 * cs(length)``).
- ``one_song_dominates`` (A2, tightened in #1342): one reference has **two or more pieces**, the
  mix has **two or more songs**, and its pieces fill **more than half** of the composed duration
  (its windows less their outgoing crossfades, the effective lengths, against the one duration
  rule). A song played once is never flagged, however long, so an ordinary class of two songs of
  unequal length stays quiet; a class of one song never is either (nothing else could play).

Hints come in mix order of their first piece, ``whole_track_repeated`` before ``one_song_dominates``
for the same reference.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from classbeat.studio.mix_plan_policy import CS, cs

WHOLE_TRACK_REPEATED: Final = "whole_track_repeated"
ONE_SONG_DOMINATES: Final = "one_song_dominates"


@dataclass(frozen=True, slots=True)
class Hint:
    code: str
    pieces: tuple[int, ...]

    def text(self) -> str:
        """The hint for the model: its code and the piece indexes only."""
        named = ", ".join(str(n) for n in self.pieces)
        if self.code == WHOLE_TRACK_REPEATED:
            return (
                f"{WHOLE_TRACK_REPEATED}: pieces {named} each play the whole of one track. Repeat "
                "only part of a song unless the instructor asked for the whole track again."
            )
        return (
            f"{ONE_SONG_DOMINATES}: pieces {named} are one track and fill more than half of the "
            "mix. Keep it only if that suits the class."
        )

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, "pieces": list(self.pieces)}


def hints(spec: Mapping[str, Any], lengths: Mapping[str, float]) -> list[Hint]:
    """The soft checks of a checked spec (see the module docstring); ``lengths`` by reference."""
    tracks = spec["tracks"]
    crossfades = [int(c) for c in spec["crossfades"]]
    windows = [cs(t["end_sec"]) - cs(t["start_sec"]) for t in tracks]
    outgoing = [*crossfades, 0]
    effective = [w - CS * outgoing[n] for n, w in enumerate(windows)]
    total = sum(windows) - CS * sum(crossfades)
    pieces: dict[str, list[int]] = {}
    for n, t in enumerate(tracks):
        pieces.setdefault(t["reference"], []).append(n)
    out: list[Hint] = []
    for reference, at in pieces.items():
        if len(at) < 2:
            continue
        length = cs(lengths[reference])
        whole = tuple(n for n in at if 10 * windows[n] >= 9 * length)
        if len(whole) >= 2:
            out.append(Hint(WHOLE_TRACK_REPEATED, whole))
        if len(pieces) >= 2 and 2 * sum(effective[n] for n in at) > total:
            out.append(Hint(ONE_SONG_DOMINATES, tuple(at)))
    return out


def texts(found: Sequence[Hint]) -> list[str]:
    return [h.text() for h in found]
