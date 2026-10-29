"""What class delete takes from operations issue X2 (#143), named in one place.

- ``resolve_orphans(db, ctx)`` is ``classbeat.operations.orphans.resolve``: ADR 0007 section 8,
  "who resolves it": marks every ``pending`` save or update row of the user whose job is over as
  ``uncertain``, in the caller's ``user_session``.
- ``NOT_ACTIONABLE_SQL`` is ``classbeat.operations.requests``' test of the one ``actionable_at``
  (section 8, "A stale call may still be running"): true while a stale call may still be sending
  requests. The protocol, Check again, Do it anyway, the operation view and class delete all use
  that single definition, so they can't disagree. A class write without a usable bound waits the
  fallback bound there: it is never actionable early.
"""

from classbeat.operations.orphans import resolve as resolve_orphans
from classbeat.operations.requests import NOT_ACTIONABLE_SQL

__all__ = ["NOT_ACTIONABLE_SQL", "resolve_orphans"]
