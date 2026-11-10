"""Whether a fitted draft keeps what the instructor stated (ADR 0029 section 4; pure).

``check_explicit(brief, spec)`` reads the **fitted spec** (the library's dict: segments of a
``kind`` with intervals) and the brief's ``explicit`` paths, and returns what is missed, each
within a tolerance (the fitting to the music moves lengths by a few seconds):

- ``length_min``: the class length, within the plan's evals tolerance (5 %);
- ``games.N``: a game of the stated type (``any``: any of the four), at the stated placement and
  of about the stated length, each draft game serving one stated game;
- ``sprints``, ``races``, ``climbs``, ``ftp_test``: the counts, within one.

A relative placement ("after climb 2") is mapped onto the draft by kind and order: ``warmup`` and
``cooldown`` are the segment of that kind, a ``climb`` is the n-th ``main`` segment, ``interval``
the n-th interval, a ``sprint`` the n-th seated burst of at most 45 s in zone 5 or more and a
``race`` the n-th seated 15 to 35 s effort in zone 6 or 7. Nothing the model wrote (names, cues)
is read, and every sentence below is built from closed values only, so it can go back to the
model as a tool error.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from classbeat.agent.brief import Brief, BriefGame, PositionPlacement, RelativePlacement
from classbeat.plan_gates import LENGTH_TOLERANCE

COUNT_TOLERANCE: Final = 1
GAME_SEC_TOLERANCE: Final = 3  # at least; 15 % of a longer game
MIN_GAME_SEC: Final = 10  # MOWL's floor: a shorter request is held to this (Not as asked)
BEFORE_COOLDOWN_REACH: Final = 3  # intervals


@dataclass(frozen=True)
class Miss:
    """One stated value the draft does not keep: ``path`` as in ``Brief.explicit``, ``code`` the
    Not as asked reason code, ``sentence`` what the model is told."""

    path: str
    code: str
    sentence: str


@dataclass(frozen=True)
class _Iv:
    index: int
    start: int
    end: int
    seg: int
    seg_kind: str
    data: Mapping[str, Any]


def _flatten(spec: Mapping[str, Any]) -> list[_Iv]:
    out: list[_Iv] = []
    clock = 0
    for s, segment in enumerate(spec.get("segments", ())):
        for data in segment.get("intervals", ()):
            sec = int(data.get("sec", 0))
            out.append(_Iv(len(out), clock, clock + sec, s, str(segment.get("kind")), data))
            clock += sec
    return out


def _is_race(i: _Iv) -> bool:
    d = i.data
    return not d.get("standing") and int(d.get("zone", 0)) >= 6 and 15 <= int(d.get("sec", 0)) <= 35


def _is_sprint(i: _Iv) -> bool:
    d = i.data
    return (
        not d.get("standing")
        and int(d.get("zone", 0)) >= 5
        and int(d.get("sec", 0)) <= 45
        and not _is_race(i)
    )


def _span(ivs: Sequence[_Iv], anchor: str, ordinal: int) -> tuple[int, int] | None:
    """The first and last interval index of the ``ordinal``-th ``anchor``, or None."""
    if anchor in ("warmup", "cooldown", "climb"):
        kind = "main" if anchor == "climb" else anchor
        segs = sorted({i.seg for i in ivs if i.seg_kind == kind})
        if ordinal > len(segs):
            return None
        mine = [i.index for i in ivs if i.seg == segs[ordinal - 1]]
        return mine[0], mine[-1]
    pick: Callable[[_Iv], bool] = {
        "interval": lambda _: True,
        "sprint": _is_sprint,
        "race": _is_race,
    }[anchor]
    found = [i.index for i in ivs if pick(i)]
    if ordinal > len(found):
        return None
    return found[ordinal - 1], found[ordinal - 1]


def _position_ok(position: str, game: _Iv, ivs: Sequence[_Iv]) -> bool:
    total = ivs[-1].end or 1
    share = game.start / total
    if position == "before_cooldown":
        cool = [i.index for i in ivs if i.seg_kind == "cooldown"]
        edge = cool[0] if cool else len(ivs)
        return edge - BEFORE_COOLDOWN_REACH <= game.index < edge
    low, high = {
        "start": (0.0, 0.2),
        "early": (0.0, 0.4),
        "middle": (0.3, 0.7),
        "late": (0.6, 1.0),
    }[position]
    return low <= share <= high


def _placed(
    placement: PositionPlacement | RelativePlacement, game: _Iv, ivs: Sequence[_Iv]
) -> bool:
    if isinstance(placement, PositionPlacement):
        return _position_ok(placement.position, game, ivs)
    span = _span(ivs, placement.anchor, placement.ordinal)
    if span is None:
        return False
    first, last = span
    if placement.relation == "after":
        return last < game.index <= last + 2
    return first <= game.index <= last  # in, replaces


def _length_ok(wanted: int | None, game: _Iv) -> bool:
    if wanted is None:
        return True
    wanted = max(wanted, MIN_GAME_SEC)
    return abs(int(game.data.get("sec", 0)) - wanted) <= max(
        GAME_SEC_TOLERANCE, round(wanted * 0.15)
    )


def _game_miss(
    n: int, want: BriefGame, free: Sequence[_Iv], ivs: Sequence[_Iv]
) -> tuple[Miss | None, _Iv | None]:
    path = f"games.{n}"
    named = "a game" if want.type == "any" else f"a {want.type} game"
    typed = [g for g in free if want.type == "any" or g.data.get("game") == want.type]
    if not typed:
        code = "game_type" if free else "game_missing"
        return Miss(path, code, f"The instructor asked for {named}: the draft has none."), None
    placed = [g for g in typed if want.placement is None or _placed(want.placement, g, ivs)]
    if not placed:
        return Miss(path, "game_placement", f"Place {named} where the instructor asked."), None
    fit = [g for g in placed if _length_ok(want.duration_sec, g)]
    if not fit:
        sentence = f"Make {named} about {want.duration_sec} s long, as the instructor asked."
        return Miss(path, "game_duration", sentence), None
    return None, fit[0]


def _count_misses(brief: Brief, ivs: Sequence[_Iv]) -> list[Miss]:
    stated = set(brief.explicit)
    counted = {
        "sprints": sum(_is_sprint(i) for i in ivs),
        "races": sum(_is_race(i) for i in ivs),
        "climbs": len({i.seg for i in ivs if i.seg_kind == "main"}),
    }
    out: list[Miss] = []
    for path, found in counted.items():
        want: int = getattr(brief, path)
        if path in stated and (
            found < want - COUNT_TOLERANCE or (path != "climbs" and found > want + COUNT_TOLERANCE)
        ):
            out.append(Miss(path, "count_off", f"The instructor asked for {want} {path}."))
    return out


def check_explicit(brief: Brief, spec: Mapping[str, Any]) -> list[Miss]:
    """What of the brief's explicit values the fitted ``spec`` misses; empty when it keeps them
    all (or when the brief states nothing)."""
    if not brief.explicit:
        return []
    ivs = _flatten(spec)
    if not ivs:
        return []
    out: list[Miss] = []
    stated = set(brief.explicit)
    total = ivs[-1].end
    if "length_min" in stated:
        target = brief.length_min * 60
        if abs(total - target) > max(30, target * LENGTH_TOLERANCE):
            out.append(
                Miss(
                    "length_min",
                    "length_off",
                    f"The instructor asked for a {brief.length_min}-minute class: "
                    f"the draft is {round(total / 60)} minutes.",
                )
            )
    if "ftp_test" in stated and not any(i.data.get("ftp_test") == brief.ftp_test for i in ivs):
        out.append(Miss("ftp_test", "ftp_missing", f"Add the {brief.ftp_test}-minute FTP test."))
    out += _count_misses(brief, ivs)
    free = [i for i in ivs if i.data.get("game") is not None]
    for n, want in enumerate(brief.games):
        if f"games.{n}" not in stated:
            continue
        miss, used = _game_miss(n, want, free, ivs)
        if miss is not None:
            out.append(miss)
        elif used is not None:
            free = [g for g in free if g.index != used.index]
    return out
