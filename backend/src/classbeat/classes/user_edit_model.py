"""The body of a user edit, ``POST /api/classes/{id}/edits`` (ADR 0042 sections 3 to 5, #1391).
Pure: no I/O.

``{base_revision_id, edit}``: the class's current revision the browser edited (a stale one is
``409 stale_revision``) and **one** operation, by ``op``:

- the workout: ``drag {block, delta_sec}``, ``move {block, direction}``, ``split {block,
  offset_sec}``, ``merge {block}``, ``set {block, field, value}``, ``duration {total_sec}``;
- the overlay: ``lock {block, scope}``, ``unlock {block}``, ``anchor {block, edge, event: {piece,
  source_ms, kind}}``, ``remove_anchor {block, edge}``, ``use_ai_value {block, field}``,
  ``clear_all``;
- the cut pins (audio edits): ``pin_set {piece, edge, requested_sec, mode}``, ``pin_confirm
  {piece, edge}`` (confirm, and re-confirm after the music changed), ``pin_clear {piece, edge}``.

``block`` is a block's flat index in the base's workout (``workout_edit_policy.blocks``); ``piece``
a piece's index in the base's mix, in play order. **No catalogue reference comes from the
browser**: the server maps a piece to its song (as the timeline sends none). Every field is
bounded and ``extra="forbid"``; whole seconds for the workout, 0.01 s for a pin.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from classbeat.studio.mix_pin_model import Edge, Mode, Sec

_STRICT = ConfigDict(extra="forbid", strict=True)
Block = Annotated[StrictInt, Field(ge=0, le=1200)]
Piece = Annotated[StrictInt, Field(ge=0, lt=40)]


class Drag(BaseModel):
    model_config = _STRICT
    op: Literal["drag"]
    block: Block
    delta_sec: Annotated[StrictInt, Field(ge=-3600, le=3600)]


class Move(BaseModel):
    model_config = _STRICT
    op: Literal["move"]
    block: Block
    direction: Literal[-1, 1]


class Split(BaseModel):
    model_config = _STRICT
    op: Literal["split"]
    block: Block
    offset_sec: Annotated[StrictInt, Field(ge=1, le=3600)]


class Merge(BaseModel):
    model_config = _STRICT
    op: Literal["merge"]
    block: Block


class SetAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["set"]
    block: Block
    field: Literal["zone", "rpm", "standing", "cue", "game"]
    value: Any  # checked as the draft schema checks it (``edit_overlay_model``)


class Duration(BaseModel):
    model_config = _STRICT
    op: Literal["duration"]
    total_sec: Annotated[StrictInt, Field(ge=60, le=4 * 3600)]


class LockBlock(BaseModel):
    model_config = _STRICT
    op: Literal["lock"]
    block: Block
    scope: Literal["all", "timing", "attributes"]


class Unlock(BaseModel):
    model_config = _STRICT
    op: Literal["unlock"]
    block: Block


class EventIn(BaseModel):
    model_config = _STRICT
    piece: Piece
    source_ms: Annotated[StrictInt, Field(ge=0, le=3_600_000)]
    kind: Literal["drop", "downbeat", "section"]


class AnchorBlock(BaseModel):
    model_config = _STRICT
    op: Literal["anchor"]
    block: Block
    edge: Edge
    event: EventIn


class RemoveAnchor(BaseModel):
    model_config = _STRICT
    op: Literal["remove_anchor"]
    block: Block
    edge: Edge


class UseAiValue(BaseModel):
    model_config = _STRICT
    op: Literal["use_ai_value"]
    block: Block
    field: Literal["zone", "rpm", "standing", "cue", "game", "boundary"]


class ClearAll(BaseModel):
    model_config = _STRICT
    op: Literal["clear_all"]


class PinSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["pin_set"]
    piece: Piece
    edge: Edge
    requested_sec: Sec
    mode: Mode


class PinConfirm(BaseModel):
    model_config = _STRICT
    op: Literal["pin_confirm"]
    piece: Piece
    edge: Edge


class PinClear(BaseModel):
    model_config = _STRICT
    op: Literal["pin_clear"]
    piece: Piece
    edge: Edge


Edit = Annotated[
    Drag
    | Move
    | Split
    | Merge
    | SetAttribute
    | Duration
    | LockBlock
    | Unlock
    | AnchorBlock
    | RemoveAnchor
    | UseAiValue
    | ClearAll
    | PinSet
    | PinConfirm
    | PinClear,
    Field(discriminator="op"),
]
WORKOUT_OPS = frozenset({"drag", "move", "split", "merge", "set", "duration"})
PIN_OPS = frozenset({"pin_set", "pin_confirm", "pin_clear"})


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision_id: UUID
    edit: Edit


class RecordOut(BaseModel):
    """An overlay record as the page sees it: its kind, its block's span and the field, never a
    catalogue reference (a pin or an anchor names its piece by index, or none when unapplied)."""

    type: str
    segment_kind: str | None = None
    span: list[int] | None = None
    field: str | None = None
    scope: str | None = None
    edge: str | None = None
    piece: int | None = None
    value: Any = None
    ai_value: Any = None
    confirmed: bool | None = None


class EditOut(BaseModel):
    revision_id: UUID
    number: int
    content_hash: str
    records: list[RecordOut]
    unapplied: list[RecordOut]
    orphaned: list[RecordOut]
    hints: list[str]


class StepOut(BaseModel):
    """An undo or redo: the pick it takes (``POST /current`` with ``expected_current_id``)."""

    revision_id: UUID
    content_hash: str


class OverlayOut(BaseModel):
    revision_id: UUID
    records: list[RecordOut]
    unapplied: list[RecordOut]
    orphaned: list[RecordOut]
    undo: StepOut | None
    redo: StepOut | None
