"""The stored shape of a studio draft's mix spec (ADR 0036 section 4.2, SR C5). Pure: no I/O.

``{"tracks": [{"reference", "start_sec", "end_sec"}], "crossfades": [int, ...]}``: the catalogue
references in the mix's order, each cut stored rounded to 0.01 s, and one whole-second crossfade
per change of track. This is the shape only; the rules against the analyses (the order, the
downbeats, the windows, the totals) are ``mix_plan_policy``'s, which made it.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    @model_validator(mode="after")
    def _a_window(self) -> Self:
        if not self.start_sec < self.end_sec:
            raise ValueError("a window ends after it starts")
        for cut in (self.start_sec, self.end_sec):
            if round(cut, 2) != cut:
                raise ValueError("cuts are stored rounded to 0.01 s")
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
