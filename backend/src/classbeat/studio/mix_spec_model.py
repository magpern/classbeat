"""The stored shape of a studio draft's mix spec (ADR 0036 section 4.2, SR C5). Pure: no I/O.

``{"tracks": [{"reference", "start_sec", "end_sec"}], "crossfades": [int, ...]}``: the pieces in the
mix's order, each a catalogue reference (one may come back in a later piece, #1342) with its cut
stored rounded to 0.01 s, and one whole-second crossfade per change of piece. This is the shape
only; the rules against the analyses (the order, the downbeats, the windows, the totals) are
``mix_plan_policy``'s, which made it.

A piece may carry the **derived** cut pins ``start_pin`` and ``end_pin`` (``mix_pin_model.CutPin``,
ADR 0042 section 5, amending A1, #1390); each is **absent** from every dump when there is none, so
a spec without a pin dumps, and hashes, byte-identically to one stored before pins existed.
"""

from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from classbeat.studio.mix_pin_model import PIN_FIELDS, CutPin
from classbeat.studio.mix_plan_policy import MAX_CROSSFADE, MAX_TRACKS

Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Cut = Annotated[float, Field(ge=0, le=3600)]
Crossfade = Annotated[int, Field(ge=0, le=MAX_CROSSFADE)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MixTrack(_Model):
    reference: Reference
    start_sec: Cut
    end_sec: Cut
    start_pin: CutPin | None = None
    end_pin: CutPin | None = None

    @model_serializer(mode="wrap")
    def _no_absent_pin(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """A pin that is not there is not dumped (module docstring); a pin's own absent fields
        neither (``CutPin.stored``)."""
        out: dict[str, Any] = handler(self)
        for name in PIN_FIELDS:
            pin: CutPin | None = getattr(self, name)
            if pin is None:
                out.pop(name, None)
            elif isinstance(out.get(name), dict):
                out[name] = {k: v for k, v in out[name].items() if v is not None}
        return out

    @model_validator(mode="after")
    def _a_window(self) -> Self:
        if not self.start_sec < self.end_sec:
            raise ValueError("a window ends after it starts")
        for cut in (self.start_sec, self.end_sec):
            if round(cut, 2) != cut:
                raise ValueError("cuts are stored rounded to 0.01 s")
        for cut, pin in ((self.start_sec, self.start_pin), (self.end_sec, self.end_pin)):
            if pin is not None and (pin.confirmed or pin.mode == "exact") and cut != pin.proposed():
                raise ValueError("a confirmed or exact pin is its cut")  # mix_plan_policy's rule
        return self


class MixSpec(_Model):
    tracks: list[MixTrack] = Field(min_length=1, max_length=MAX_TRACKS)
    crossfades: list[Crossfade] = Field(max_length=MAX_TRACKS - 1)

    @model_validator(mode="after")
    def _one_per_change(self) -> Self:
        if len(self.crossfades) != len(self.tracks) - 1:
            raise ValueError("one crossfade per change of track")
        return self

    def references(self) -> list[str]:
        return [t.reference for t in self.tracks]
