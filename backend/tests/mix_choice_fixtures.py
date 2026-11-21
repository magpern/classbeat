"""Fixed proposals for the mix-choice tests (ADR 0036 amendment B, #1367): checked specs over four
240 s tracks at 120 BPM (2 s bars from 0), with cuts on bars so ``checked`` is a fixed point."""

from typing import Any

from classbeat.spotdown.cut_candidates_model import Candidate
from classbeat.spotdown.timing_inputs_model import TimingClass
from classbeat.studio.mix_choice_model import Proposal
from classbeat.studio.mix_facts_model import TrackFacts

ORDER = ("a", "b", "c", "d")
TARGET = 880


def phrases(*points: float) -> tuple[Candidate, ...]:
    return tuple(
        Candidate(p, kind, 0.9, None, True)
        for p in points
        for kind in ("phrase_start", "phrase_end")
    )


def track(
    timing: TimingClass | None = "exact", length: float = 240.0, *points: float
) -> TrackFacts:
    return TrackFacts(length, 120.0, 0.0, timing, phrases(*(points or (32.0, 96.0, 180.0))))


FACTS = {r: track() for r in ORDER}


def spec(*cuts: tuple[str, float, float], crossfades: list[int]) -> dict[str, Any]:
    return {
        "tracks": [{"reference": r, "start_sec": a, "end_sec": b} for r, a, b in cuts],
        "crossfades": crossfades,
    }


# Every song cut but c; composes to 796 s.
PLAIN = spec(
    ("a", 0.0, 180.0), ("b", 20.0, 220.0), ("c", 0.0, 240.0), ("d", 40.0, 240.0),
    crossfades=[8, 6, 10],
)  # fmt: skip
# a plays twice, not adjacent (A3); 828 s.
REPEAT = spec(
    ("a", 0.0, 100.0), ("b", 20.0, 220.0), ("a", 120.0, 240.0), ("c", 0.0, 240.0),
    ("d", 40.0, 240.0), crossfades=[8, 6, 8, 10],
)  # fmt: skip
# a loops (A4): its two pieces back to back, the join on phrase points 96 and 32; 840 s.
LOOP = spec(
    ("a", 0.0, 96.0), ("a", 32.0, 160.0), ("b", 20.0, 220.0), ("c", 0.0, 240.0),
    ("d", 40.0, 240.0), crossfades=[0, 8, 6, 10],
)  # fmt: skip


def proposal(value: dict[str, Any], target: int = TARGET) -> Proposal:
    return Proposal(ORDER, value, target)
