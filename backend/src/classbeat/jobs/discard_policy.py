"""Which ended job discards its class (ADR 0038): the pure rule, no I/O.

A class made on the start screen is created with its ``interpret_request`` job; once the reading is
stored, the hand-off starts its first design (``design_draft`` or ``design_with_songs``). When that
first creation ends ``failed`` or ``cancelled`` before anything was made, the class is an empty
shell, and ``classbeat.jobs.discard`` removes it in the transaction that ends the job.

This is an allowlist: the class goes only when every fact below says it holds nothing worth keeping.
Anything not named here keeps it (another kind, another ending, a version, a second attempt, a saved
workout, an external write or any other row the class owns).
"""

from dataclasses import dataclass
from typing import Final

READING: Final = "interpret_request"
FIRST_DESIGNS: Final = frozenset({"design_draft", "design_with_songs"})
# ``expired`` is left out: only a job waiting for approval expires, and it has a song list.
ENDINGS: Final = frozenset({"failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class Facts:
    """What the class and its jobs hold, read under the class lock in the ending's transaction.

    ``other_jobs``: the class's jobs besides the ending one, in any state. ``readings``: those of
    them that are the class's completed reading whose hand-off key the ending job carries (so the
    ending job is that reading's own first design). ``kept``: a row in any table that keeps a class
    (``classbeat.jobs.discard.KEEPING``)."""

    kind: str
    state: str
    credit_state: str
    has_version: bool  # a current revision or an approved Swedebeat match
    has_reading: bool  # ``classes.interpretation`` is stored
    revisions: int
    other_jobs: int
    readings: int
    kept: bool


def candidate(kind: str, state: str) -> bool:
    """Whether an ending is worth looking at the class for (read before any class lock)."""
    return state in ENDINGS and (kind == READING or kind in FIRST_DESIGNS)


def may_discard(facts: Facts) -> bool:
    """True only for the empty shell of a failed or stopped first creation."""
    if not candidate(facts.kind, facts.state) or facts.credit_state == "committed":
        return False
    if facts.has_version or facts.revisions != 0 or facts.kept:
        return False
    if facts.kind == READING:  # the reading was the class's only job and stored nothing
        return facts.other_jobs == 0 and not facts.has_reading
    return facts.other_jobs == 1 and facts.readings == 1  # the reading, then its first design
