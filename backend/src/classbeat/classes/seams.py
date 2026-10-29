"""What class delete takes from operations issue X2 (#143), named in one place.

X2 builds ``classbeat.operations.orphans.resolve`` and the ``actionable_at`` of an operation. They
are not on ``main`` when C2 (#144) is built, so C2 calls these two seams and nothing else of X2's.
When #143 has merged, the follow-up is to point ``resolve_orphans`` at the module directly and to
read ``actionable_at`` from the operations view instead of ``ACTIONABLE_AT_SQL``.

- ``resolve_orphans(db, ctx)``: ADR 0007 section 8, "who resolves it": marks every ``pending`` save
  or update row of the user whose job is over as ``uncertain``. Until the module exists it does
  nothing, which only makes delete more cautious: such a row stays ``pending`` and so refuses the
  delete as ``call_may_be_running``.
- ``ACTIONABLE_AT_SQL``: the moment a stale call can no longer be running (section 8, "A stale call
  may still be running"). A row whose call returned (``call_ended_at >= pending_at``) is
  actionable at ``pending_at`` (at once); any other row at ``pending_at + bound_seconds + 30 s``.
  ``bound_seconds`` comes from the row's ``request_context``; without a usable one the bound is
  taken as ``FALLBACK_BOUND_SECONDS``, so a row of unknown bound is never treated as safe early.
"""

import importlib
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context

ORPHANS_MODULE: Final = "classbeat.operations.orphans"
GRACE_SECONDS: Final = 30
FALLBACK_BOUND_SECONDS: Final = 3600

ACTIONABLE_AT_SQL: Final = f"""(
    CASE WHEN call_ended_at IS NOT NULL AND call_ended_at >= coalesce(pending_at, created_at)
        THEN coalesce(pending_at, created_at)
    ELSE coalesce(pending_at, created_at) + make_interval(secs => (
        CASE WHEN jsonb_typeof(request_context -> 'bound_seconds') = 'number'
            THEN (request_context ->> 'bound_seconds')::double precision
            ELSE {FALLBACK_BOUND_SECONDS} END) + {GRACE_SECONDS})
    END)"""


async def resolve_orphans(db: AsyncSession, ctx: UserContext) -> None:
    """The hook for ``classbeat.operations.orphans.resolve(db, ctx)`` (issue #143)."""
    require_context(ctx)
    try:
        module: Any = importlib.import_module(ORPHANS_MODULE)
    except ModuleNotFoundError as error:
        if error.name != ORPHANS_MODULE:  # a broken import inside the module is not "absent"
            raise
        return
    await module.resolve(db, ctx)
