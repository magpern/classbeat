"""The instructor's direct edits of a draft's workout (ADR 0042 section 3, #1391). Pure: no I/O.

The editable unit is the **block**: one interval of the stored draft (``agent.schemas.Interval``),
addressed by its flat index in class order (``blocks``). Every operation works in whole seconds on
a copy of the stored content and returns ``Edited``: the new content and, per new block, the old
block it came from (``origin``), so the overlay's records follow the edit exactly
(``edit_overlay_policy.moved``). Refusals are ``EditRefused`` by name:

- ``drag``: block *k* grows and *k+1* shrinks by the same amount (the total is kept), clamped to
  each block's minimum and to 3600 s; across a segment boundary it moves that boundary;
- ``move``: swaps a block with its neighbour in the same segment (the total is kept);
- ``split``: at an offset inside a block, two blocks with the same attributes, each at least the
  minimum;
- ``merge``: a block and the next one of its segment, with the first block's attributes;
- ``set_attribute``: ``zone``, ``rpm``, ``standing``, ``cue`` or ``game``; never the timing, never
  the music;
- ``duration``: the class's new total; the **last** block that is neither timing-locked nor an FTP
  test takes the difference.

A block with ``ftp_test`` (its ``sec`` must match the test) is not dragged, split or merged
(``ftp_test_fixed``) and ``duration`` skips it. A timing-locked block (``lock`` of scope ``all`` or
``timing``) is not dragged, moved, split or merged, and an attribute-locked one is not set
(``block_locked``). A block's minimum is 1 s, 10 s for a game and the block after one
(``mowl.workout.validate``'s rule); everything else is ``agent.design.check``'s, which every
result goes through (the caller). An edit that changes nothing is ``nothing_changed``.
"""

import copy
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final

from classbeat.design.edit_overlay_model import ATTRIBUTES

MAX_SEC: Final = 3600
MAX_INTERVALS: Final = 60  # per segment, ``agent.schemas.Segment``
GAME_MIN_SEC: Final = 10  # ``mowl.workout.MIN_GAME_SEC``


class EditRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Block:
    segment: int
    index: int
    kind: str
    start_ms: int
    end_ms: int
    interval: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Edited:
    content: dict[str, Any]
    origin: tuple[int | None, ...]  # per new block, the old block it came from


def blocks(content: dict[str, Any]) -> list[Block]:
    """The content's blocks in class order with their class-time spans."""
    out: list[Block] = []
    at = 0
    for s, segment in enumerate(content["segments"]):
        for i, interval in enumerate(segment["intervals"]):
            end = at + int(interval["sec"]) * 1000
            out.append(Block(s, i, str(segment["kind"]), at, end, interval))
            at = end
    return out


def total_sec(content: dict[str, Any]) -> int:
    return sum(int(i["sec"]) for s in content["segments"] for i in s["intervals"])


def _flat(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in content["segments"] for i in s["intervals"]]


def min_sec(flat: list[dict[str, Any]], at: int) -> int:
    game = flat[at].get("game") or (at > 0 and flat[at - 1].get("game"))
    return GAME_MIN_SEC if game else 1


def _at(content: dict[str, Any], at: int) -> Block:
    found = blocks(content)
    if not 0 <= at < len(found):
        raise EditRefused("invalid_input")
    return found[at]


def _free(at: int, flat: list[dict[str, Any]], timing_locked: Collection[int]) -> None:
    if at in timing_locked:
        raise EditRefused("block_locked")
    if flat[at].get("ftp_test") is not None:
        raise EditRefused("ftp_test_fixed")


def _same(n: int) -> tuple[int | None, ...]:
    return tuple(range(n))


def drag(
    content: dict[str, Any], at: int, delta_sec: int, timing_locked: Collection[int] = ()
) -> Edited:
    """Move the end of block ``at`` by ``delta_sec`` (module docstring)."""
    out = copy.deepcopy(content)
    flat = _flat(out)
    if not 0 <= at < len(flat) - 1:
        raise EditRefused("invalid_input")
    for n in (at, at + 1):
        _free(n, flat, timing_locked)
    left, right = flat[at], flat[at + 1]
    low = max(min_sec(flat, at) - left["sec"], right["sec"] - MAX_SEC)
    high = min(MAX_SEC - left["sec"], right["sec"] - min_sec(flat, at + 1))
    delta = max(low, min(high, delta_sec))
    if delta == 0:
        raise EditRefused("nothing_changed")
    left["sec"] += delta
    right["sec"] -= delta
    return Edited(out, _same(len(flat)))


def move(
    content: dict[str, Any], at: int, direction: int, timing_locked: Collection[int] = ()
) -> Edited:
    """Swap block ``at`` with its neighbour ``direction`` (-1 or 1) in the same segment."""
    block = _at(content, at)
    other = at + direction
    out = copy.deepcopy(content)
    intervals = out["segments"][block.segment]["intervals"]
    j = block.index + direction
    if direction not in (-1, 1) or not 0 <= j < len(intervals):
        raise EditRefused("invalid_input")
    if at in timing_locked or other in timing_locked:
        raise EditRefused("block_locked")
    intervals[block.index], intervals[j] = intervals[j], intervals[block.index]
    origin = list(_same(len(_flat(out))))
    origin[at], origin[other] = other, at
    return Edited(out, tuple(origin))


def split(
    content: dict[str, Any], at: int, offset_sec: int, timing_locked: Collection[int] = ()
) -> Edited:
    """Split block ``at`` ``offset_sec`` after its start (module docstring)."""
    block = _at(content, at)
    out = copy.deepcopy(content)
    flat = _flat(out)
    _free(at, flat, timing_locked)
    intervals = out["segments"][block.segment]["intervals"]
    first = intervals[block.index]
    minimum = max(min_sec(flat, at), GAME_MIN_SEC if first.get("game") else 1)
    if not minimum <= offset_sec <= first["sec"] - minimum:
        raise EditRefused("split_out_of_range")
    if len(intervals) >= MAX_INTERVALS:
        raise EditRefused("too_many_blocks")
    second = {**copy.deepcopy(first), "sec": first["sec"] - offset_sec}
    first["sec"] = offset_sec
    intervals.insert(block.index + 1, second)
    n = len(flat)
    origin = (*range(at + 1), at, *range(at + 1, n))
    return Edited(out, origin)


def merge(
    content: dict[str, Any],
    at: int,
    timing_locked: Collection[int] = (),
    attribute_locked: Collection[int] = (),
) -> Edited:
    """Merge block ``at`` with the next block of its segment (module docstring)."""
    block = _at(content, at)
    out = copy.deepcopy(content)
    flat = _flat(out)
    intervals = out["segments"][block.segment]["intervals"]
    if block.index + 1 >= len(intervals):
        raise EditRefused("invalid_input")
    for n in (at, at + 1):
        _free(n, flat, timing_locked)
    if at + 1 in attribute_locked:
        raise EditRefused("block_locked")  # its attributes would be replaced by the first's
    first, second = intervals[block.index], intervals[block.index + 1]
    if first["sec"] + second["sec"] > MAX_SEC:
        raise EditRefused("block_too_long")
    first["sec"] += second["sec"]
    del intervals[block.index + 1]
    origin = (*range(at + 1), *range(at + 2, len(flat)))
    return Edited(out, origin)


def set_attribute(
    content: dict[str, Any],
    at: int,
    field: str,
    value: object,
    attribute_locked: Collection[int] = (),
) -> Edited:
    """Set one attribute of block ``at``; never its timing."""
    if field not in ATTRIBUTES:
        raise EditRefused("invalid_input")
    block = _at(content, at)
    if at in attribute_locked:
        raise EditRefused("block_locked")
    out = copy.deepcopy(content)
    interval = out["segments"][block.segment]["intervals"][block.index]
    if interval.get(field, "" if field == "cue" else None) == value:
        raise EditRefused("nothing_changed")
    if value is None:
        interval.pop(field, None)  # no game: the stored form omits it
    else:
        interval[field] = copy.deepcopy(value)
    return Edited(out, _same(len(_flat(out))))


def duration(
    content: dict[str, Any], new_total_sec: int, timing_locked: Collection[int] = ()
) -> Edited:
    """The class's new total, taken by the last free block (module docstring)."""
    out = copy.deepcopy(content)
    flat = _flat(out)
    delta = new_total_sec - total_sec(out)
    if delta == 0:
        raise EditRefused("nothing_changed")
    for at in range(len(flat) - 1, -1, -1):
        if at in timing_locked or flat[at].get("ftp_test") is not None:
            continue
        sec = flat[at]["sec"] + delta
        if not min_sec(flat, at) <= sec <= MAX_SEC:
            raise EditRefused("duration_out_of_range")
        flat[at]["sec"] = sec
        return Edited(out, _same(len(flat)))
    raise EditRefused("block_locked")
