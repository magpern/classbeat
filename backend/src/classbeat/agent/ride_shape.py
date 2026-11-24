"""The shape of a ride: an ordered list of sections (ADR 0029 section 4 amendment, #1485; pure).

An instructor often describes a class in parts ("starts slow and builds up, a hilly section, 5-7
short heavy bursts, back on the flats ..."). Summing those into ``climbs`` and ``sprints`` loses the
order, the instructor's range ("5-7", "a few") and the meaning (a classbeat sprint is a seated
burst; "heavy" is resistance), so the brief keeps an **ordered shape**: ``Brief.shape``, at most
``MAX_SECTIONS`` sections, each of a ``kind`` and the closed modifiers the request gave.

A section holds closed values and numbers only, never text, so no model-written text comes back to
a model through the brief:

- ``kind``: ``warmup``, ``climb``, ``efforts``, ``flat``, ``recovery`` or ``cooldown``;
- ``trend`` (``steady``, ``rising``, ``falling``: "builds up" is rising) and ``effort`` (``easy``,
  ``moderate``, ``hard``: this section's own effort, not the class's ``intensity``);
- ``count``: a ``CountRange`` of hills (``climb``) or efforts (``efforts``), and of no other kind;
  an exact number is ``{n, n}``, a range keeps its ends ("5-7"), "a few" is ``{2, 4}``;
- ``length`` (``short`` or ``long``: each effort's), ``load`` (``light`` or ``heavy``:
  resistance) and ``minutes`` (a stated length of the section).

``MAX_SHAPE_BYTES`` bounds the shape's size in the stored brief (jsonb text), so the worst case of
a 2000-character request still fits ``classes.brief`` (4096 bytes): ``fitting`` keeps the leading
sections that fit. A section is stored without its absent fields.
"""

import json
from collections.abc import Sequence
from typing import Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from classbeat.agent.schemas import STRICT

SectionKind = Literal["warmup", "climb", "efforts", "flat", "recovery", "cooldown"]
Trend = Literal["steady", "rising", "falling"]
Effort = Literal["easy", "moderate", "hard"]
Length = Literal["short", "long"]
Load = Literal["light", "heavy"]

MAX_SECTIONS: Final = 12
MAX_COUNT: Final = 20
MAX_MINUTES: Final = 120
MAX_SHAPE_BYTES: Final = 640
COUNTED: Final = ("climb", "efforts")  # the kinds that carry a count


class CountRange(BaseModel):
    """How many hills or efforts: ``min`` to ``max`` (an exact number is both)."""

    model_config = STRICT

    min: int = Field(ge=1, le=MAX_COUNT)
    max: int = Field(ge=1, le=MAX_COUNT)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError("a count range runs from its smaller number to its larger")
        return self


class SectionFields(BaseModel):
    """The fields of a section, shared by the brief's (``ShapeSection``) and the interpreter's."""

    model_config = STRICT

    kind: SectionKind
    trend: Trend | None = None
    effort: Effort | None = None
    count: CountRange | None = None
    length: Length | None = None
    load: Load | None = None
    minutes: int | None = Field(default=None, ge=1, le=MAX_MINUTES)

    @model_validator(mode="after")
    def _count_only_where_counted(self) -> Self:
        if self.count is not None and self.kind not in COUNTED:
            raise ValueError("only a climb or an efforts section has a count")
        return self


class ShapeSection(SectionFields):
    """One section of ``Brief.shape``."""

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        out: dict[str, Any] = handler(self)
        return {k: v for k, v in out.items() if v is not None}


def shape_bytes(sections: Sequence[ShapeSection]) -> int:
    """The shape's size in the stored brief: jsonb text (``", "`` and ``": "``), UTF-8 bytes."""
    dumped = [s.model_dump(mode="json") for s in sections]
    return len(json.dumps(dumped, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


def fitting(sections: Sequence[ShapeSection]) -> list[ShapeSection]:
    """The leading sections that fit ``MAX_SECTIONS`` and ``MAX_SHAPE_BYTES``."""
    kept = list(sections[:MAX_SECTIONS])
    while kept and shape_bytes(kept) > MAX_SHAPE_BYTES:
        kept.pop()
    return kept


def largest() -> list[ShapeSection]:
    """The largest shape a brief can hold (``interpret_policy.worst_case_brief``)."""
    full = ShapeSection(
        kind="efforts",
        trend="rising",
        effort="hard",
        count=CountRange(min=20, max=20),
        length="short",
        load="heavy",
        minutes=MAX_MINUTES,
    )
    return fitting([full] * MAX_SECTIONS)
