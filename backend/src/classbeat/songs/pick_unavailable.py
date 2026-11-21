"""How a song stage ends when spotdown cannot answer (ADR 0040 amendment "spotdown is the only
analysis path", item 4; #1352, #1354). The one place the stages turn a cause into a
``JobFailure``.

- ``failure_of`` and ``unreachable``: an ``Unreachable`` answer. The category is
  ``outage_policy.unreachable_category``
  (``analysis_unavailable``, ``analysis_failed`` or ``internal``) and ``outage_policy.log_message``
  is logged **at every site**, so the admin view can tell an operator's fault from an outage.
- ``unavailable``: an outage found by the stage itself (a provider outage, a wait that does not fit
  the budget, the budget spent, a ``rate_limited`` read): ``analysis_unavailable``.

Both ``unreachable`` and ``unavailable`` also set the budget row's flag for an unavailable ending,
so a later run of the same attempt ends at once (``pick_match_budget``); it is reset with the
attempt (``jobs.spotdown_budget_reset``). They return the failure; the caller raises it.
"""

import logging

from classbeat.jobs import JobFailure, JobRun
from classbeat.songs import pick_match_budget as budget
from classbeat.spotdown import outage_policy
from classbeat.spotdown.failure_policy import Unreachable

_log = logging.getLogger("classbeat.songs.spotdown")


def failure_of(answer: Unreachable) -> JobFailure:
    """The failure for an unreachable spotdown; logs the fixed line (no budget row: phase 2 and a
    revise carry have none)."""
    _log.warning(outage_policy.log_message(answer.reason))
    return JobFailure(outage_policy.unreachable_category(answer.reason))


async def unreachable(run: JobRun, answer: Unreachable) -> JobFailure:
    """``failure_of`` and the budget row's flag when the category is retryable."""
    failure = failure_of(answer)
    if failure.args == (outage_policy.UNAVAILABLE,):
        await budget.mark_unavailable(run)
    return failure


async def unavailable(run: JobRun) -> JobFailure:
    """The failure for an outage the stage found itself."""
    await budget.mark_unavailable(run)
    return JobFailure(outage_policy.UNAVAILABLE)
