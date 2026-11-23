"""What a revision records of the SwedeBeat mix it was designed on (ADR 0016 section 3; ADR 0040
section 6; split out of ``design.music``). Pure: no I/O."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MixFacts:
    """For the user only: the mix's id and name, the analysed and the listed length, and the
    songs (0-based) whose beat 1 is an estimate. Never model input. The map's spotdown
    ``provenance`` record (plan section 4a: kept per class; every analysis has one, #1299)."""

    mix_id: str
    name: str
    duration: float
    listed_duration: float
    unsure_beat_one: tuple[int, ...]
    provenance: dict[str, Any]
