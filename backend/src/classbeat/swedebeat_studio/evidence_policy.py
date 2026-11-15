"""Failures as evidence (ADR 0036 section 3.2): what a studio match does with the songs SwedeBeat
did not find. Pure: no I/O.

- The first answer's ``missing`` tracks are matched **once more** after a pause
  (``retry_slots``). A track found (or unsure) on the second try was a transient failure and keeps
  that answer (``merge``). An ``unsure`` answer is SwedeBeat's own, not a failure: it is not
  asked again, and it still counts as not found (``SAME_SONG``, ``verdict_policy``).
- When every sent track is still ``missing`` after that, with at least ``ALL_MISSING_MIN`` (3)
  sent, the state is ``suspected_unavailable``: possibly an outage, **not** proof. There are no
  automatic swaps; the user chooses Try again later or Swap anyway.
- Any other answer is ``ok``, however many are missing: a high miss rate alone never blocks.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from classbeat.swedebeat_match.verdict_policy import ALL_MISSING_MIN, Verdict

State = Literal["ok", "suspected_unavailable"]
OK: Final = "ok"
SUSPECTED: Final = "suspected_unavailable"


@dataclass(frozen=True, slots=True)
class Evidence:
    """A match's counts (sent tracks, missing on the first answer, missing at the end) and state."""

    state: State
    sent: int
    missing_first: int
    missing: int


def retry_slots(first: Sequence[Verdict]) -> list[int]:
    """The tracks to match once more: those the first answer called ``missing``."""
    return [slot for slot, found in enumerate(first) if found.status == "missing"]


def merge(first: Sequence[Verdict], retried: Mapping[int, Verdict]) -> list[Verdict]:
    """The first answer with each retried track's second answer, unless that is ``missing`` too."""
    merged = list(first)
    for slot, again in retried.items():
        if not 0 <= slot < len(merged) or merged[slot].status != "missing":
            raise ValueError("only a missing track is matched again")
        if again.status != "missing":
            merged[slot] = again
    return merged


def evidence(first: Sequence[Verdict], final: Sequence[Verdict]) -> Evidence:
    """The state of a match whose first answer was ``first`` and whose merged one is ``final``."""
    if len(first) != len(final):
        raise ValueError("one verdict per sent track")
    missing = sum(found.status == "missing" for found in final)
    suspected = len(final) >= ALL_MISSING_MIN and missing == len(final)
    return Evidence(
        SUSPECTED if suspected else OK,
        len(final),
        sum(found.status == "missing" for found in first),
        missing,
    )


def really_missing(final: Sequence[Verdict]) -> list[int]:
    """The tracks a swap may replace: not ``found`` after the retry (``unsure`` included)."""
    return [slot for slot, found in enumerate(final) if found.status != "found"]
