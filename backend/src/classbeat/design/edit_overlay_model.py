"""The edit overlay of a draft revision, ``class_revisions.edit_overlay`` (ADR 0042 sections 3
and 5; #1391). Pure: no I/O.

``{"version": 1, "records": [...]}``, at most ``MAX_RECORDS`` records and ``MAX_BYTES`` of canonical
JSON; an overlay with no record is stored as NULL (``stored``), so the hash returns to the plain
form. Each record is one of:

- ``override``: the instructor's value for one field of a block, ``{target, field, value,
  ai_value_at_set}``. ``field`` is an attribute (``zone``, ``rpm``, ``standing``, ``cue``,
  ``game``) or ``boundary``, whose value is the block's **end** in class time, whole seconds in
  milliseconds (what a drag set, as fitted). ``ai_value_at_set`` is the model's value it replaced,
  the one **Use AI value** restores (a regeneration updates it to the model's new value);
- ``lock``: automation must not change the block within ``scope`` (``all``, ``timing``,
  ``attributes``);
- ``anchor``: a block's ``start`` or ``end`` sits on a musical event of one piece of the mix
  (``event``: the piece by reference and occurrence, the analysed file's SHA-256, the position in
  that file in milliseconds, the kind of event and the analysis it was read from);
- ``pin``: a cut pin (``mix_pin_model.PinRecord``) with ``set_against``, the analysed file's
  SHA-256 when it was set, so an unconfirmed pin also goes to "listen again" when the file changes.

A **target** is ``{segment_kind, span: [start_ms, end_ms)}`` at the time of setting: blocks have no
stable id across regeneration (``edit_overlay_policy`` reconciles them). Values are checked as the
draft schema checks them (``agent.schemas.Interval``), so an override can never hold a value a draft
could not.
"""

from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from classbeat.agent.schemas import Interval
from classbeat.design.revision_hash import canonical
from classbeat.studio.mix_pin_model import PinRecord, Reference, Sha256

MAX_RECORDS: Final = 200
MAX_BYTES: Final = 32768
VERSION: Final = 1
MAX_MS: Final = 86_400_000
ATTRIBUTES: Final = ("zone", "rpm", "standing", "cue", "game")
SegmentKind = Literal["warmup", "main", "recovery", "cooldown"]
Field_ = Literal["zone", "rpm", "standing", "cue", "game", "boundary"]
Ms = Annotated[StrictInt, Field(ge=0, le=MAX_MS)]
_FROZEN = ConfigDict(extra="forbid", frozen=True)
_PROBE: Final[dict[str, Any]] = {"sec": 60, "zone": 1, "rpm": [80, 90], "standing": False}


class Target(BaseModel):
    model_config = _FROZEN

    segment_kind: SegmentKind
    span: Annotated[list[Ms], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def _a_span(self) -> Self:
        if self.span[0] >= self.span[1]:
            raise ValueError("a span starts before it ends")
        return self

    @property
    def start(self) -> int:
        return self.span[0]

    @property
    def end(self) -> int:
        return self.span[1]


def _valid_value(field: str, value: object) -> bool:
    if field == "boundary":
        return type(value) is int and 0 < value <= MAX_MS and value % 1000 == 0
    if field == "game" and value is None:
        return True
    try:
        found = Interval.model_validate({**_PROBE, field: value})
    except ValidationError:
        return False
    return getattr(found, field) == value  # a cue the schema would clean is not stored as sent


class Override(BaseModel):
    model_config = _FROZEN

    type: Literal["override"]
    target: Target
    field: Field_
    value: Any
    ai_value_at_set: Any

    @model_validator(mode="after")
    def _values(self) -> Self:
        valid = _valid_value(self.field, self.value)
        if not (valid and _valid_value(self.field, self.ai_value_at_set)):
            raise ValueError("an override holds values a draft could")
        return self


class Lock(BaseModel):
    model_config = _FROZEN

    type: Literal["lock"]
    target: Target
    scope: Literal["all", "timing", "attributes"]

    def covers_timing(self) -> bool:
        return self.scope in ("all", "timing")

    def covers_attributes(self) -> bool:
        return self.scope in ("all", "attributes")


class Event(BaseModel):
    model_config = _FROZEN

    reference: Reference
    occurrence: Annotated[StrictInt, Field(ge=0, lt=40)]
    sha256: Sha256
    source_ms: Ms
    kind: Literal["drop", "downbeat", "section"]
    map_version: Annotated[StrictInt, Field(ge=1, le=1000)]


class Anchor(BaseModel):
    model_config = _FROZEN

    type: Literal["anchor"]
    target: Target
    edge: Literal["start", "end"]
    event: Event


class OverlayPin(PinRecord):
    """A cut pin record of the overlay (module docstring)."""

    type: Literal["pin"]
    set_against: Sha256 | None = None


Record = Annotated[Override | Lock | Anchor | OverlayPin, Field(discriminator="type")]


class EditOverlay(BaseModel):
    model_config = _FROZEN

    version: Literal[1]
    records: Annotated[list[Record], Field(max_length=MAX_RECORDS)]

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        pins = [r.target() for r in self.records if isinstance(r, OverlayPin)]
        if len(set(pins)) != len(pins):
            raise ValueError("a cut is pinned once")
        if len(canonical(self.model_dump(mode="json", exclude_none=True))) > MAX_BYTES:
            raise ValueError("an overlay is at most 32 KB")
        return self

    def pins(self) -> tuple[OverlayPin, ...]:
        return tuple(r for r in self.records if isinstance(r, OverlayPin))

    def stored(self) -> dict[str, Any] | None:
        """The stored form: NULL (``None``) when there is no record."""
        if not self.records:
            return None
        return self.model_dump(mode="json", exclude_none=True)


EMPTY: Final = EditOverlay(version=VERSION, records=[])


def of_records(records: list[Any]) -> EditOverlay:
    """A new overlay of ``records``; ``ValueError`` (a ``ValidationError``) past the bounds."""
    return EditOverlay(version=VERSION, records=records)


def parsed(raw: object) -> EditOverlay:
    """The stored ``edit_overlay`` (``None``: NULL, the empty overlay); ``ValueError`` for one
    that does not read (the caller fails closed)."""
    if raw is None:
        return EMPTY
    found = EditOverlay.model_validate(raw)
    if not found.records:
        raise ValueError("an empty overlay is stored as NULL")
    return found


def storable(overlay: EditOverlay) -> dict[str, Any] | None:
    """``overlay.stored()`` once it reads back as itself through ``parsed`` (the bounds, the
    record shapes); ``ValueError`` otherwise, so a write never stores what a read would refuse."""
    raw = overlay.stored()
    if parsed(raw).stored() != raw:
        raise ValueError("the overlay does not read back as stored")
    return raw
