"""How a studio track's or a mix's spotdown analysis ends a job when it gives no map (ADR 0040
amendment "spotdown is the only analysis path", item 4; #1353). Each helper logs one fixed line
(the category and a fixed label, never a URL, a host, a scope, a sha256, a reference or a title)
and returns the ``JobFailure`` to raise:

- ``unreachable(reason)``: an ``Unreachable`` answer, or spotdown not usable in this worker, with
  ``outage_policy``'s category and its ``log_message``; called at **every** such site;
- ``unavailable(label)``: ``analysis_unavailable`` (retried) for a budget spent, a wait that does
  not fit, the mix's 180 s cap, a ``provider_error`` after its retry or an outage flag found;
- ``failed(label)``: ``analysis_failed`` (not retried) for a status spotdown answered about this
  music, or a refused or unmappable document.

Any other error is not caught anywhere on the path: it ends the job ``internal`` as everywhere.
"""

import logging

from classbeat.jobs import JobFailure
from classbeat.spotdown import outage_policy
from classbeat.spotdown.failure_policy import UnreachableReason

logger = logging.getLogger(__name__)


def unreachable(reason: UnreachableReason) -> JobFailure:
    logger.warning(outage_policy.log_message(reason))
    return JobFailure(outage_policy.unreachable_category(reason))


def unavailable(label: str) -> JobFailure:
    logger.warning("spotdown ending: category=%s reason=%s", outage_policy.UNAVAILABLE, label)
    return JobFailure(outage_policy.UNAVAILABLE)


def failed(label: str) -> JobFailure:
    logger.warning("spotdown ending: category=%s reason=%s", outage_policy.FAILED, label)
    return JobFailure(outage_policy.FAILED)
