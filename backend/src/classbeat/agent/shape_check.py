"""Whether a draft follows the brief's ride shape (ADR 0029 section 4 amendment of 2026-11-24,
#1493; pure).

``check_shape(brief, spec, tags)`` reads the **fitted spec** and the section tag of each of its
segments (``tags``, one per segment, ``None`` for a segment with no tag: the app's own warm-up
and cool-down when the shape has none), and returns what is missed. Each miss is of one section
(path ``shape.N``, as the reading's items), so a remaining miss is recorded as Not as asked for
that section (``recorded``): ``shape_off`` for a missing, misordered or miscounted section, and
its own reason ``shape_untagged`` for a draft that names no section (#1516):

- ``shape_untagged``: the brief has a shape and no segment names a section (one miss, on
  ``shape.0``, whatever the number of sections);
- ``shape_missing``: no segment is tagged with the section;
- ``shape_order``: a segment of the section comes after one of a later section, so the tags are
  not non-decreasing through the draft;
- ``shape_tag``: a segment names a section the shape has not (path ``shape``, never recorded);
- ``shape_count``: an ``efforts`` section's intervals (in its segments, zone 5 or more and at most
  90 s each) or a ``climb`` section's ``main`` segments are outside the section's range.

Nothing the model wrote (names, cues) is read, and every sentence is built from closed values
only, so it can go back to the model as a tool error. A section the brief gave no ``count`` has
no count to keep. The check is the same for every stage that follows a shape: cycling design and
revise, the soundless pair and the composer's design with songs.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.agent.brief import Brief
from classbeat.agent.explicit_check import Miss
from classbeat.agent.interpretation_record import MAX_FOUND
from classbeat.agent.ride_shape import ShapeSection

EFFORT_MIN_ZONE: Final = 5
EFFORT_MAX_SEC: Final = 90
SHAPE_OFF: Final = "shape_off"  # the Not as asked reason code of a section the draft misses
UNTAGGED: Final = "shape_untagged"  # the reason code of a draft that names no section at all
CODES: Final = frozenset(
    {"shape_untagged", "shape_missing", "shape_order", "shape_count", "shape_tag"}
)


def _is_effort(interval: Mapping[str, Any]) -> bool:
    return (
        int(interval.get("zone", 0)) >= EFFORT_MIN_ZONE
        and int(interval.get("sec", 0)) <= EFFORT_MAX_SEC
    )


def _range(low: int, high: int) -> str:
    return str(low) if low == high else f"{low} to {high}"


def _found(section: ShapeSection, segments: Sequence[Mapping[str, Any]]) -> int | None:
    """How many hills (``main`` segments) or efforts the section's segments hold; None when the
    section has no count."""
    if section.kind == "climb":
        return sum(1 for s in segments if s.get("kind") == "main")
    if section.kind == "efforts":
        return sum(_is_effort(i) for s in segments for i in s.get("intervals", ()))
    return None


def _count_sentence(n: int, section: ShapeSection, found: int) -> str:
    wanted = _range(section.count.min, section.count.max) if section.count else ""
    if section.kind == "climb":
        return f"Section {n} (climb) needs {wanted} hills, each one main segment: it has {found}."
    return (
        f"Section {n} (efforts) needs {wanted} short hard intervals (zone {EFFORT_MIN_ZONE} or "
        f"more, at most {EFFORT_MAX_SEC} s each): its segments have {found}."
    )


def _late(tags: Sequence[int]) -> set[int]:
    """The sections with a segment after a segment of a later section."""
    out: set[int] = set()
    highest = -1
    for tag in tags:
        if tag < highest:
            out.add(tag)
        highest = max(highest, tag)
    return out


def check_shape(brief: Brief, spec: Mapping[str, Any], tags: Sequence[int | None]) -> list[Miss]:
    """What of the brief's shape the fitted ``spec`` with its segments' ``tags`` misses; empty
    when it follows the shape (or the brief has none)."""
    shape = brief.shape
    if not shape:
        return []
    segments: Sequence[Mapping[str, Any]] = spec.get("segments", ())
    last = len(shape) - 1
    tagged = [t for t in tags[: len(segments)] if t is not None and t <= last]
    stray = any(t is not None and t > last for t in tags)
    out: list[Miss] = []
    if stray:
        out.append(Miss("shape", "shape_tag", f"A section index is from 0 to {last}."))
    if not tagged:
        sentence = (
            f"The brief has a shape: give every segment its section index, from 0 to {last}, "
            "in order."
        )
        return [*out, Miss("shape.0", "shape_untagged", sentence)]  # one item: the draft has none
    late = _late(tagged)
    for n, section in enumerate(shape):
        mine = [s for s, t in zip(segments, tags, strict=False) if t == n]
        if not mine:
            sentence = f"Section {n} ({section.kind}) has no segment: tag one with section {n}."
            out.append(Miss(f"shape.{n}", "shape_missing", sentence))
        elif n in late:
            out.append(
                Miss(
                    f"shape.{n}",
                    "shape_order",
                    f"Section {n} has a segment after a later section's: the sections run in "
                    "order, 0 first.",
                )
            )
        elif section.count is not None:
            found = _found(section, mine)
            if found is not None and not section.count.min <= found <= section.count.max:
                sentence = _count_sentence(n, section, found)
                out.append(Miss(f"shape.{n}", "shape_count", sentence, min(found, MAX_FOUND)))
    return out


def recorded(miss: Miss) -> Miss:
    """A remaining shape miss as it is recorded (Not as asked ``shape_off``, one per section;
    ``shape_untagged`` for a draft with no tag, one on ``shape.0``); any other miss as it is."""
    if miss.code == UNTAGGED:
        return miss
    if miss.code in CODES:
        return Miss(miss.path, SHAPE_OFF, miss.sentence, miss.found)
    return miss
