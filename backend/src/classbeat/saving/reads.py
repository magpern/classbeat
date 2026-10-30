"""The save and update reconcilers' one database read: the spec of the revision an operation
wrote.

A reconciler gets a session factory and the user's context (``ReconcileInput``), not a
transaction: it runs outside any transaction in the worker (step 4 of the protocol) and in the
web process (the user's Check again). It needs the revision's content to rebuild the spec it
compares programs with, and nothing in the operation row can hold it (2048 bytes). So this
module opens a read-only ``user_session`` of its own (it is on the session guard's allowlist for
that, ``tests/session_scan.py``): one ``SELECT`` under the user's row security, with ``owner_id``
and the operation's ``class_id``, checked against the operation's stored ``content_hash``. It
writes nothing; every write of a save goes through the job's fence or the user's actions.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.saving import approval

Factory = async_sessionmaker[AsyncSession]


async def saved_spec(
    factory: Factory, ctx: UserContext, class_id: UUID, context: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The spec of the operation's revision (its class, its stored hash), or None when it is
    gone, changed or no longer a valid draft."""
    require_context(ctx)
    try:
        revision_id = UUID(str(context["revision_id"]))
        content_hash = bytes.fromhex(str(context["content_hash"]))
    except (KeyError, ValueError):
        return None
    async with user_session(factory, ctx) as db:
        found = await approval.revision(db, ctx, class_id, revision_id, content_hash)
    return found.spec if isinstance(found, approval.Revision) else None
