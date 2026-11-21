"""Which job category an unreachable spotdown ends a job in (ADR 0040 amendment "spotdown is the
only analysis path", item 4, #1352). Pure: no I/O.

Every ``UnreachableReason`` (``failure_policy``) maps to exactly one category, listed here once:

- ``analysis_unavailable`` (retried with backoff): the reason is about the service or its
  deployment, not about this music: ``not_configured``, ``source_off``, ``source_disabled``
  (spotdown's YouTube sourcing is off; the owner may switch it on), ``blocked``, ``connection``,
  ``unauthorized`` and ``server_error``;
- ``analysis_failed`` (not retried): spotdown answered, but about this request or its answer:
  ``too_large``, ``rejected`` and ``bad_answer``;
- ``internal`` (not retried): ``conflict``, an ``Idempotency-Key`` used for another body, is
  classbeat's own bug.

Decisions on the review notes of #1360:

- ``blocked`` and ``unauthorized`` stay ``analysis_unavailable`` (the ADR's list): both are an
  operator's to fix and the retries cost nothing, and the page must not tell the user which. The
  admin view cannot tell them apart from an outage by the category, so a caller logs
  ``log_message(reason)``, which carries the fixed reason label next to the category. The label is a
  member of the closed ``UnreachableReason`` set, never a URL, a host, a scope or a sha256.
- A **lease-lost requeue** (the reaper; ``lease_lost`` is not in ``jobs.errors.RETRYABLE``) counts
  an attempt but does not end in a retryable category, so it does **not** reset the spotdown wait
  budget or the outage flag: the next attempt inherits what was spent (conservative; at worst it
  ends ``analysis_unavailable`` and is retried with a fresh budget). Only a run that ends in a
  ``RETRYABLE`` category, and Approve starting a new phase, reset them (#1353, #1354).
"""

from typing import Final

from classbeat.spotdown.failure_policy import UnreachableReason

UNAVAILABLE: Final = "analysis_unavailable"
FAILED: Final = "analysis_failed"
INTERNAL: Final = "internal"

_CATEGORY: Final[dict[str, str]] = {
    "not_configured": UNAVAILABLE,
    "source_off": UNAVAILABLE,
    "source_disabled": UNAVAILABLE,
    "blocked": UNAVAILABLE,
    "connection": UNAVAILABLE,
    "unauthorized": UNAVAILABLE,
    "server_error": UNAVAILABLE,
    "too_large": FAILED,
    "rejected": FAILED,
    "bad_answer": FAILED,
    "conflict": INTERNAL,
}
# ``tests/test_spotdown_outage_policy.py`` checks one entry per ``UnreachableReason``.


def unreachable_category(reason: UnreachableReason) -> str:
    """The job category of an ``Unreachable`` with this reason (``internal`` for a value outside
    the closed set, which the type does not allow)."""
    return _CATEGORY.get(reason, INTERNAL)


def log_message(reason: UnreachableReason) -> str:
    """The fixed line to log with the category: its name and the reason label, nothing else."""
    label = reason if reason in _CATEGORY else "unknown"
    return f"spotdown unreachable: category={unreachable_category(reason)} reason={label}"
