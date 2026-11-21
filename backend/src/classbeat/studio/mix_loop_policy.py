"""The loop joins of a mix spec (ADR 0036 amendment A4, #1342). Pure: no I/O.

A **loop join** is a change of piece between two pieces of one reference (``mix_repeat_calc``).
``mix_plan_policy.checked`` calls ``refusal`` at step 6 of A7: after coverage, before the lengths.

**Part 1 of #1342 (plain repeats): every loop join is refused with ``loop_needs_exact_timing``.**
A4.1 lets a loop join through only for a track whose **stored** timing decision is ``exact``, and
refuses it for ``approximate``, ``unusable`` or **no stored timing decision**. The validator does
not receive any timing decision yet (``TrackFacts`` has the length, BPM and first downbeat only),
so for it every track has none, and A4.1 names the reason. The rest of A4 (the 30 ms phrase
tolerance against #1328's trusted candidates, ``loop_crossfade_not_zero``, ``MIN_LOOP_BARS`` and
the run's 60 s) is part 2, with #1328. A **non-adjacent** repeat is an ordinary piece (A3) and
never reaches this module.
"""

from collections.abc import Sequence

from classbeat.studio.mix_repeat_calc import loop_joins

NEEDS_EXACT = "loop_needs_exact_timing"


def refusal(references: Sequence[str]) -> tuple[str, str] | None:
    """The first loop join's ``(reason, detail)`` in mix order, or None without a loop join."""
    joins = loop_joins(references)
    if not joins:
        return None
    n = joins[0]
    return (
        NEEDS_EXACT,
        f"pieces {n} and {n + 1} are the same track back to back (a loop), which needs the "
        "track's exact timing; put another track between them",
    )
