"""The edit overlay applied to a workout (ADR 0042 sections 3 and 8, #1391). Pure: no I/O.

- ``match``: **reconciliation**. The block of a workout a record's target now names: of the
  blocks of the target's segment kind, the one with the greatest class-time overlap with the
  target's span, if that overlap is at least half the span (``MIN_OVERLAP``); else none, and the
  record is **unapplied** (listed, kept, without effect until re-targeted or cleared).
- ``reapplied``: the overlay re-applied to a workout automation made (a regeneration, or B4a's remap
  after an audio edit), in the **fixed order**: locks are checked, then boundary overrides applied,
  then attribute overrides, then anchors refit; the caller then runs ``agent.design.check``. A lock
  whose block changed within its scope raises ``OverlayViolated`` (``overlay_violated`` for a
  regeneration, ``locked_block_conflict`` for an audio edit), naming the record. After a
  regeneration an override's ``ai_value_at_set`` becomes the model's new value (the screen shows
  "Your edit kept (AI suggested Green)"); after a remap a boundary override follows the remap (the
  boundary moved with the music, as every boundary does). An anchor whose event has left the mix
  (``event_ms`` is ``None``) is **orphaned**: no effect, listed.
- ``moved``: the records carried exactly through a direct edit (``workout_edit_policy.Edited``'s
  ``origin``), never by overlap: a moved block keeps its records, a split block's go to both halves
  (its end's to the second), a merge keeps the first block's attributes and drops the records of
  the second block and of the vanished boundary.
- ``retargeted``: the placed records with their targets on the fitted workout (``check`` may snap a
  boundary): a boundary override stores the **fitted** end, what the user will get.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from classbeat.design.edit_overlay_model import (
    ATTRIBUTES,
    Anchor,
    Event,
    Lock,
    OverlayPin,
    Override,
    Record,
    Target,
)
from classbeat.design.workout_edit_policy import MAX_SEC, Block, Edited, blocks, min_sec

MIN_OVERLAP: Final = 0.5
OVERLAY_VIOLATED: Final = "overlay_violated"
LOCKED_BLOCK_CONFLICT: Final = "locked_block_conflict"

Placed = list[tuple[Record, int]]  # a record and the flat index of its block


class OverlayViolated(Exception):
    def __init__(self, reason: str, record: Record) -> None:
        super().__init__(reason)
        self.reason = reason
        self.record = record


@dataclass(frozen=True)
class Applied:
    content: dict[str, Any]
    placed: Placed
    unapplied: tuple[Record, ...] = ()
    orphaned: tuple[Anchor, ...] = ()
    pins: tuple[OverlayPin, ...] = field(default=())  # carried as they are (``mix_pin_policy``)


def match(target: Target, found: Sequence[Block]) -> int | None:
    """The block ``target`` names now (module docstring), by flat index."""
    best: tuple[int, int] | None = None
    for n, block in enumerate(found):
        if block.kind != target.segment_kind:
            continue
        overlap = min(block.end_ms, target.end) - max(block.start_ms, target.start)
        if overlap > 0 and (best is None or overlap > best[0]):
            best = (overlap, n)
    if best is None or best[0] < MIN_OVERLAP * (target.end - target.start):
        return None
    return best[1]


def _attributes(interval: Mapping[str, Any]) -> dict[str, Any]:
    return {f: interval.get(f, "" if f == "cue" else None) for f in ATTRIBUTES}


def _kept(lock: Lock, before: Block, after: Block) -> bool:
    if lock.covers_timing() and (before.start_ms, before.end_ms) != (after.start_ms, after.end_ms):
        return False
    return not (
        lock.covers_attributes() and _attributes(before.interval) != _attributes(after.interval)
    )


def _flat(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in content["segments"] for i in s["intervals"]]


def set_end(content: dict[str, Any], at: int, end_ms: int, fixed: frozenset[int]) -> bool:
    """Block ``at``'s end set to ``end_ms`` (whole seconds), the next block taking the difference;
    False (nothing changed) when it cannot: the last block, a fixed (timing-locked or FTP) block,
    a minimum or 3600 s."""
    flat = _flat(content)
    found = blocks(content)
    if not 0 <= at < len(flat) - 1 or end_ms % 1000:
        return False
    delta = (end_ms - found[at].end_ms) // 1000
    if delta == 0:
        return True
    if {at, at + 1} & fixed or any(flat[n].get("ftp_test") is not None for n in (at, at + 1)):
        return False
    left, right = flat[at]["sec"] + delta, flat[at + 1]["sec"] - delta
    if not (min_sec(flat, at) <= left <= MAX_SEC and min_sec(flat, at + 1) <= right <= MAX_SEC):
        return False
    flat[at]["sec"], flat[at + 1]["sec"] = left, right
    return True


def _timing_locked(placed: Placed) -> frozenset[int]:
    return frozenset(n for r, n in placed if isinstance(r, Lock) and r.covers_timing())


def reapplied(
    content: dict[str, Any],
    base: dict[str, Any],
    records: Sequence[Record],
    *,
    event_ms: Callable[[Event], int | None],
    remap: bool = False,
) -> Applied:
    """``records`` (set against ``base``) re-applied to ``content`` (module docstring); ``content``
    is changed in place and returned."""
    reason = LOCKED_BLOCK_CONFLICT if remap else OVERLAY_VIOLATED
    before, now = blocks(base), blocks(content)
    placed: Placed = []
    unapplied: list[Record] = []
    orphaned: list[Anchor] = []
    for lock in (r for r in records if isinstance(r, Lock)):
        was = match(lock.target, before)
        if was is None:
            unapplied.append(lock)
            continue
        at = match(lock.target, now)
        if at is None or not _kept(lock, before[was], now[at]):
            raise OverlayViolated(reason, lock)
        placed.append((lock, at))
    fixed = _timing_locked(placed)
    overrides = [r for r in records if isinstance(r, Override)]
    for record in sorted((r for r in overrides if r.field == "boundary"), key=lambda r: r.value):
        at = match(record.target, blocks(content))
        if at is None:
            unapplied.append(record)
        elif remap:
            placed.append((record, at))  # it follows the remap (module docstring)
        else:
            model_end = blocks(content)[at].end_ms
            if set_end(content, at, record.value, fixed):
                placed.append((record.model_copy(update={"ai_value_at_set": model_end}), at))
            else:
                unapplied.append(record)
    for record in (r for r in overrides if r.field != "boundary"):
        at = match(record.target, blocks(content))
        if at is None:
            unapplied.append(record)
            continue
        interval = _flat(content)[at]
        model_value = _attributes(interval)[record.field]
        if record.value is None:
            interval.pop(record.field, None)
        else:
            interval[record.field] = record.value
        update = {} if remap else {"ai_value_at_set": model_value}
        placed.append((record.model_copy(update=update), at))
    for anchor in (r for r in records if isinstance(r, Anchor)):
        at = match(anchor.target, blocks(content))
        when = event_ms(anchor.event)
        if when is None:
            orphaned.append(anchor)
            continue
        edge_at = at if anchor.edge == "end" else (None if at is None else at - 1)
        if at is None or edge_at is None or edge_at < 0:
            unapplied.append(anchor)
        elif set_end(content, edge_at, round(when / 1000) * 1000, fixed):
            placed.append((anchor, at))
        else:
            unapplied.append(anchor)
    pins = tuple(r for r in records if isinstance(r, OverlayPin))
    return Applied(content, placed, tuple(unapplied), tuple(orphaned), pins)


def moved(
    records: Sequence[Record], base: dict[str, Any], edited: Edited
) -> tuple[Placed, list[Record]]:
    """``records`` carried through the direct edit ``edited`` of ``base`` (module docstring), and
    the records that do not name a block of ``base`` (kept as they are: unapplied)."""
    before = blocks(base)
    news: dict[int, list[int]] = {}
    for n, old in enumerate(edited.origin):
        if old is not None:
            news.setdefault(old, []).append(n)
    placed: Placed = []
    loose: list[Record] = []
    for record in records:
        if isinstance(record, OverlayPin):
            continue
        was = match(record.target, before)
        if was is None:
            loose.append(record)
            continue
        targets = news.get(was, [])
        ends_here = (isinstance(record, Override) and record.field == "boundary") or (
            isinstance(record, Anchor) and record.edge == "end"
        )
        if len(targets) > 1:  # a split: an end goes to the second half, a start to the first
            if ends_here:
                targets = targets[-1:]
            elif isinstance(record, Anchor):
                targets = targets[:1]
        merged = was + 1 < len(before) and was + 1 not in news and len(targets) == 1
        if merged and ends_here:
            continue  # the boundary it named is gone
        placed.extend((record, n) for n in targets)
    return placed, loose


def retargeted(placed: Placed, fitted: dict[str, Any]) -> list[Record]:
    """The placed records with their targets on the fitted workout (module docstring)."""
    found = blocks(fitted)
    out: list[Record] = []
    for record, at in placed:
        block = found[at]
        target = Target(segment_kind=block.kind, span=[block.start_ms, block.end_ms])  # pyright: ignore[reportArgumentType]
        update: dict[str, Any] = {"target": target}
        if isinstance(record, Override) and record.field == "boundary":
            update["value"] = block.end_ms
        out.append(record.model_copy(update=update))
    return out


def event_ms(
    spec: Mapping[str, Any], analysed: Mapping[str, str | None]
) -> Callable[[Event], int | None]:
    """The class time of an event in a studio mix spec: the event's piece (its reference's
    ``occurrence``-th piece) starts where the pieces before it end, less their crossfades; ``None``
    (an orphaned anchor) when the piece is not in the mix, its analysed file is not the event's
    (``analysed``: each reference's SHA-256) or the event is outside the piece's window."""
    starts: list[tuple[str, int, float, float, float]] = []
    seen: dict[str, int] = {}
    at = 0.0
    tracks: list[Mapping[str, Any]] = list(spec.get("tracks", ()))
    fades: list[Any] = list(spec.get("crossfades", ()))
    for n, track in enumerate(tracks):
        reference = str(track["reference"])
        start, end = float(track["start_sec"]), float(track["end_sec"])
        if n:
            at -= float(fades[n - 1])
        starts.append((reference, seen.get(reference, 0), at, start, end))
        seen[reference] = seen.get(reference, 0) + 1
        at += end - start

    def find(event: Event) -> int | None:
        for reference, occurrence, class_sec, start, end in starts:
            if (reference, occurrence) == (event.reference, event.occurrence):
                if analysed.get(reference) != event.sha256:
                    return None
                source = event.source_ms / 1000
                if not start <= source < end:
                    return None
                return round((class_sec + source - start) * 1000)
        return None

    return find
