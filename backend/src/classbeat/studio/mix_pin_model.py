"""A cut pin and its record in the edit overlay (ADR 0042 section 5, amending ADR 0036 A1, #1390).
Pure: no I/O.

- ``CutPin``: what a pinned cut carries in the stored mix spec (``MixTrack.start_pin`` /
  ``end_pin``): ``requested_sec`` (the instructor's request, 0.01 s), ``mode`` (``bar``: snapped to
  the bar grid; ``exact``: kept as requested), ``confirmed`` ("Confirm cut" after listening) and,
  only when confirmed, ``confirmed_sec`` (the effective cut then) and ``confirmed_against`` (the hex
  SHA-256 of the analysed file then). A confirmed pin has both, an unconfirmed one neither.
  ``stored`` is its one stored form: the optional fields are **absent** when there is none.
- ``PinRecord``: the overlay's record of one pin, the source of truth (E2 stores the overlay): a
  ``CutPin`` with its **target** ``{reference, occurrence, edge}``: the song's catalogue reference,
  which of its pieces in mix order (0 for the first, A3 allows repeats) and which cut of it
  (``start`` or ``end``). The target is the song, never a position.

Every number is stored rounded to 0.01 s; ``extra="forbid"``, so no other field is accepted. The
rules (where a pin applies, the effective cut, the refusals) are ``mix_pin_policy``'s and
``mix_plan_policy``'s.
"""

from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["bar", "exact"]
Edge = Literal["start", "end"]
EDGES: Final[tuple[Edge, ...]] = ("start", "end")
FIELD_OF: Final[dict[Edge, str]] = {"start": "start_pin", "end": "end_pin"}
PIN_FIELDS: Final = frozenset(FIELD_OF.values())
Sec = Annotated[float, Field(ge=0, le=3600)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


def _rounded(value: float | None) -> bool:
    return value is None or round(value, 2) == value


class CutPin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    requested_sec: Sec
    mode: Mode
    confirmed: bool
    confirmed_sec: Sec | None = None
    confirmed_against: Sha256 | None = None

    @model_validator(mode="after")
    def _a_pin(self) -> Self:
        if not (_rounded(self.requested_sec) and _rounded(self.confirmed_sec)):
            raise ValueError("a pin is stored rounded to 0.01 s")
        has = (self.confirmed_sec is not None, self.confirmed_against is not None)
        if has != (self.confirmed, self.confirmed):
            raise ValueError("a confirmed pin has its cut and file; an unconfirmed one neither")
        return self

    def proposed(self) -> float:
        """The cut the pin asks for: the confirmed one, else the request."""
        return self.requested_sec if self.confirmed_sec is None else self.confirmed_sec

    def stored(self) -> dict[str, Any]:
        """The one stored form: the optional fields absent when there is none."""
        return self.model_dump(mode="json", exclude_none=True, include=set(CutPin.model_fields))


class PinRecord(CutPin):
    """One overlay record (module docstring)."""

    reference: Reference
    occurrence: Annotated[int, Field(ge=0, lt=40)]  # a mix has at most 40 pieces (MAX_TRACKS)
    edge: Edge

    def target(self) -> tuple[str, int, Edge]:
        return self.reference, self.occurrence, self.edge

    def pin(self) -> CutPin:
        return CutPin.model_validate(CutPin.stored(self))

    def stored(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
