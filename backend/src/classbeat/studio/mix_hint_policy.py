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

``inexact_cuts`` (ADR 0036 amendment B5, #1372) is the stage's soft hint ``cut_on_inexact_track``:
the pieces of a track whose stored timing is not ``exact`` (``approximate``, ``unusable`` or no
decision) that do not play it whole (``[0, length]`` in whole hundredths, as
``mix_choice_policy.is_plain`` reads whole). Never a refusal: ``mix_choice_policy.applied`` keeps
such a track whole whatever the proposal says.

``pin_hints`` are the cut pins' soft checks (ADR 0042 section 5, #1390), each naming one piece and
its ``edge``, in mix order, start before end:

- ``off_beat_cut``: a pinned cut off the bar grid of a track that has a trusted one (``exact``,
  ``mix_cut_policy.snaps``): an ``exact`` pin, or a confirmed one. A
  ``bar`` pin is on the grid by definition.
- ``pin_moved``: the same target's pinned cut is not where it was in ``before`` (a reanalysis
  re-snapped an unconfirmed ``bar`` pin), with ``moved_sec``, so nothing moves silently.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from classbeat.studio.mix_cut_policy import snapped, snaps
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_pin_policy import occurrences, pins_of, targets
from classbeat.studio.mix_plan_policy import CS, cs

WHOLE_TRACK_REPEATED: Final = "whole_track_repeated"
ONE_SONG_DOMINATES: Final = "one_song_dominates"
CUT_ON_INEXACT_TRACK: Final = "cut_on_inexact_track"
OFF_BEAT_CUT: Final = "off_beat_cut"
PIN_MOVED: Final = "pin_moved"


@dataclass(frozen=True, slots=True)
class Hint:
    code: str
    pieces: tuple[int, ...]
    edge: str | None = None  # a pin's hint: which cut of the piece
    moved_sec: float | None = None  # ``pin_moved``: by how much

    def text(self) -> str:
        """The hint for the model: its code and the piece indexes only."""
        named = ", ".join(str(n) for n in self.pieces)
        if self.code == OFF_BEAT_CUT:
            return f"{OFF_BEAT_CUT}: the pinned {self.edge} cut of piece {named} is off the beat."
        if self.code == PIN_MOVED:
            return (
                f"{PIN_MOVED}: the pinned {self.edge} cut of piece {named} moved by reanalysis: "
                f"{self.moved_sec:+.2f} s."
            )
        if self.code == WHOLE_TRACK_REPEATED:
            return (
                f"{WHOLE_TRACK_REPEATED}: pieces {named} each play the whole of one track. Repeat "
                "only part of a song unless the instructor asked for the whole track again."
            )
        if self.code == CUT_ON_INEXACT_TRACK:
            return (
                f"{CUT_ON_INEXACT_TRACK}: pieces {named} cut a track whose timing is not exact. "
                "Such a track plays once, whole (start_sec 0, end_sec its length): the "
                "instructor's mix keeps it whole whatever the cut."
            )
        return (
            f"{ONE_SONG_DOMINATES}: pieces {named} are one track and fill more than half of the "
            "mix. Keep it only if that suits the class."
        )

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "pieces": list(self.pieces)}
        if self.edge is not None:
            out["edge"] = self.edge
        if self.moved_sec is not None:
            out["moved_sec"] = self.moved_sec
        return out


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


def pin_hints(
    spec: Mapping[str, Any],
    facts: Mapping[str, TrackFacts],
    before: Mapping[str, Any] | None = None,
) -> list[Hint]:
    """The pins' soft checks of a checked spec (module docstring); ``facts`` by reference,
    ``before`` the spec it was derived again from (``None``: no move to report)."""
    was = targets(before) if before is not None else {}
    at = occurrences(spec)
    out: list[Hint] = []
    for n, edge, pin in pins_of(spec):
        known = facts[at[n][0]]
        cut = float(spec["tracks"][n][f"{edge}_sec"])
        free = pin.mode == "exact" or pin.confirmed  # a bar pin is on the grid
        if free and snaps(known) and cs(snapped(cut, known)) != cs(cut):
            out.append(Hint(OFF_BEAT_CUT, (n,), edge))
        old = was.get((*at[n], edge))
        if old is not None and cs(old) != cs(cut):
            out.append(Hint(PIN_MOVED, (n,), edge, (cs(cut) - cs(old)) / CS))
    return out


def inexact_cuts(spec: Mapping[str, Any], facts: Mapping[str, TrackFacts]) -> list[Hint]:
    """``cut_on_inexact_track`` on a checked spec (see the module docstring); ``facts`` by
    reference. At most one hint, naming every such piece in mix order."""
    cut = tuple(
        n
        for n, t in enumerate(spec["tracks"])
        if (found := facts[t["reference"]]).timing_class != "exact"
        and (cs(t["start_sec"]) != 0 or cs(t["end_sec"]) != cs(found.length))
    )
    return [Hint(CUT_ON_INEXACT_TRACK, cut)] if cut else []


def texts(found: Sequence[Hint]) -> list[str]:
    return [h.text() for h in found]
