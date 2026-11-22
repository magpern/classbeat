"""The overlay records a user edit reads and writes (ADR 0042 section 4, #1391). Pure: no I/O.

- ``listed``: the records without effect on a draft, **unapplied** (their block or piece is gone)
  and **orphaned** anchors (their event left the mix), for the edit's answer and the overlay read.
- ``overrides``, ``anchors``, ``locks``: a block's placed records of one kind.
- ``ai_value``: the AI value a new override keeps (an earlier override's, else the block's now).
- ``restore`` and ``restored``: a block, or every placed override, back to the AI value
  (``use_ai_value``, ``clear_all``).
"""

import copy
from typing import Any

from classbeat.design.edit_overlay_model import (
    Anchor,
    EditOverlay,
    Lock,
    OverlayPin,
    Override,
    Record,
)
from classbeat.design.edit_overlay_policy import Placed, event_ms, match, set_end
from classbeat.design.workout_edit_policy import EditRefused, blocks
from classbeat.studio.mix_pin_policy import occurrences


def listed(
    content: dict[str, Any],
    spec: dict[str, Any],
    overlay: EditOverlay,
    analysed: dict[str, str | None],
) -> tuple[tuple[Record, ...], tuple[Anchor, ...]]:
    found = blocks(content)
    pieces = set(occurrences(spec))
    where = event_ms(spec, analysed)
    unapplied: list[Record] = []
    orphaned: list[Anchor] = []
    for record in overlay.records:
        if isinstance(record, OverlayPin):
            if (record.reference, record.occurrence) not in pieces:
                unapplied.append(record)
        elif isinstance(record, Anchor) and where(record.event) is None:
            orphaned.append(record)
        elif match(record.target, found) is None:
            unapplied.append(record)
    return tuple(unapplied), tuple(orphaned)


def overrides(placed: Placed, at: int, name: str) -> Placed:
    return [(r, n) for r, n in placed if isinstance(r, Override) and n == at and r.field == name]


def anchors(placed: Placed, at: int, edge: str) -> Placed:
    return [(r, n) for r, n in placed if isinstance(r, Anchor) and n == at and r.edge == edge]


def locks(placed: Placed, at: int) -> Placed:
    return [(r, n) for r, n in placed if isinstance(r, Lock) and n == at]


def without(placed: Placed, gone: Placed) -> Placed:
    """``placed`` less ``gone``; removing nothing is ``nothing_changed``."""
    if not gone:
        raise EditRefused("nothing_changed")
    return [p for p in placed if p not in gone]


def ai_value(placed: Placed, at: int, name: str, now: object) -> object:
    for record, _ in overrides(placed, at, name):
        if isinstance(record, Override):
            return record.ai_value_at_set
    return now


def restore(content: dict[str, Any], record: Override, at: int, timing: frozenset[int]) -> None:
    """Block ``at``'s field back to the override's AI value (in place)."""
    if record.field == "boundary":
        end = record.ai_value_at_set
        if not isinstance(end, int) or not set_end(content, at, end, timing):
            raise EditRefused("block_locked")
        return
    interval = blocks(content)[at].interval
    if record.ai_value_at_set is None:
        interval.pop(record.field, None)
    else:
        interval[record.field] = copy.deepcopy(record.ai_value_at_set)


def restored(content: dict[str, Any], placed: Placed, timing: frozenset[int]) -> dict[str, Any]:
    """``content`` with every placed override back to its AI value, boundaries first."""
    out = copy.deepcopy(content)
    found: list[tuple[Override, int]] = [(r, n) for r, n in placed if isinstance(r, Override)]
    for record, at in sorted(found, key=lambda p: (p[0].field != "boundary", p[1])):
        restore(out, record, at, timing)
    return out
