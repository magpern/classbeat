"""The edit overlay as the page reads it (ADR 0042 section 4, #1391). Pure: no I/O.

A record goes out as ``RecordOut``: its kind, its block's segment kind and span, the field, the
values. **No catalogue reference leaves**: a pin or an anchor names its piece by index in the
draft's mix (play order), ``None`` when the piece is no longer in it.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from classbeat.classes.user_edit_model import RecordOut
from classbeat.design.edit_overlay_model import Anchor, Lock, OverlayPin, Override, Record
from classbeat.studio.mix_pin_policy import occurrences


def _piece(spec: Mapping[str, Any], reference: str, occurrence: int) -> int | None:
    try:
        return occurrences(spec).index((reference, occurrence))
    except (ValueError, KeyError, TypeError):
        return None


def record_out(record: Record, spec: Mapping[str, Any]) -> RecordOut:
    if isinstance(record, OverlayPin):
        return RecordOut(
            type="pin",
            edge=record.edge,
            piece=_piece(spec, record.reference, record.occurrence),
            value=record.requested_sec,
            confirmed=record.confirmed,
        )
    span = list(record.target.span)
    kind = record.target.segment_kind
    if isinstance(record, Override):
        return RecordOut(
            type="override",
            segment_kind=kind,
            span=span,
            field=record.field,
            value=record.value,
            ai_value=record.ai_value_at_set,
        )
    if isinstance(record, Lock):
        return RecordOut(type="lock", segment_kind=kind, span=span, scope=record.scope)
    assert isinstance(record, Anchor)  # noqa: S101 - the union's last member
    piece = _piece(spec, record.event.reference, record.event.occurrence)
    return RecordOut(
        type="anchor",
        segment_kind=kind,
        span=span,
        edge=record.edge,
        piece=piece,
        value=record.event.source_ms,
    )


def records_out(records: Sequence[Record], spec: Mapping[str, Any]) -> list[RecordOut]:
    return [record_out(r, spec) for r in records]
