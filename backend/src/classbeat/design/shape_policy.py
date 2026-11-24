"""The shape of the ride in a reading and in a revise (ADR 0029 section 4 amendment, #1485; pure:
no database, no network, no model).

``InterpretedRequest.shape`` is the ordered sections the request described. The policy turns it
into ``Brief.shape`` and one interpretation item per section, path ``shape.N`` (0-based, the
section in order), with the section as a small closed object, its origin and its kept quote, so the
page can show each section in the instructor's words.

- the shape is cycling only: on a running class every explicit section is **Not as asked**
  ``cycling_only`` and none is kept;
- the brief keeps the leading sections that fit ``ride_shape.MAX_SHAPE_BYTES`` (the stored brief's
  size guard); an explicit section beyond that is Not as asked ``shape_too_large``;
- ``Brief.explicit`` names ``shape`` when any kept section was stated explicitly;
- a **revise** reports the whole shape after the change: it replaces the base's when it differs
  (a reported section the request did not state, equal to the base's, changes nothing, as for the
  knobs), and the path stays or becomes stated when a reported section is explicit.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from classbeat.agent.interpretation_record import InterpretationItem, keep_quote
from classbeat.agent.interpreted_shape import InterpretedSection
from classbeat.agent.ride_shape import ShapeSection, fitting

CYCLING_ONLY: Final = "cycling_only"
SHAPE_TOO_LARGE: Final = "shape_too_large"
SHAPE: Final = "shape"


@dataclass(frozen=True)
class ShapeReading:
    """The sections the brief takes, whether any was stated, and the items to record."""

    sections: list[ShapeSection] = field(default_factory=lambda: list[ShapeSection]())
    explicit: bool = False
    items: list[InterpretationItem] = field(default_factory=lambda: list[InterpretationItem]())


def _item(
    n: int, section: InterpretedSection, request: str, reason: str | None
) -> InterpretationItem:
    return InterpretationItem(
        path=f"{SHAPE}.{n}",
        origin="not_as_asked" if reason else section.origin,
        reason_code=reason,
        value=section.closed().model_dump(mode="json"),
        quote=keep_quote(section.quote, request),
    )


def read_shape(read: Sequence[InterpretedSection], request: str, sport: str) -> ShapeReading:
    """The shape of a new request (or of a revise's reading, see ``revised``): the sections the
    brief takes and the items, one per section in order. Items of a section that is dropped are
    Not as asked only when the instructor stated it; an inferred one is dropped quietly."""
    if not read:
        return ShapeReading()
    closed = [s.closed() for s in read]
    kept = [] if sport == "running" else fitting(closed)
    reason = CYCLING_ONLY if sport == "running" else SHAPE_TOO_LARGE
    items: list[InterpretationItem] = []
    for n, section in enumerate(read):
        if n < len(kept):
            items.append(_item(n, section, request, None))
        elif section.origin == "explicit":
            items.append(_item(n, section, request, reason))
    stated = any(s.origin == "explicit" for s in read[: len(kept)])
    return ShapeReading(kept, stated, items)


def revised(
    base: Sequence[ShapeSection], read: Sequence[InterpretedSection], sport: str
) -> ShapeReading:
    """A revise's reading of the shape: ``sections`` is what the class has after the change (the
    base's, when the request reports none or only repeats it) and ``explicit`` whether the
    request stated a section. The items are the sections the change touched (a section equal to
    the base's at its place keeps what the class's own record says of it) and every Not as asked;
    no quote is kept (the revise request is not ``brief.text``)."""
    if not read:
        return ShapeReading(list(base))
    got = read_shape(read, "", sport)
    missed = [i for i in got.items if i.origin == "not_as_asked"]
    if not got.sections or got.sections == list(base):
        return ShapeReading(list(base), got.explicit, missed)
    items = [
        i.model_copy(update={"quote": None})
        for n, i in enumerate(got.items)
        if i.origin == "not_as_asked" or n >= len(base) or got.sections[n] != base[n]
    ]
    return ShapeReading(got.sections, got.explicit, items)
