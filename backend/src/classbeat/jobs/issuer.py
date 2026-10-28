"""The job issuer of ``UserContext`` (ADR 0002 section 1, ADR 0005 section 3).

The claim functions return only ``(job_id, owner_id, lease_version)``: there is no ambient
authority. From that row alone this module

1. asks ``classbeat.auth`` whether the owner exists and is not disabled (guard 8: only auth reads
   the account tables);
2. issues a context with ``role="user"`` (a job never carries admin authority, whoever owns it),
   no entitlements (M3a), and the owner's connection refs read through
   ``classbeat.connections`` in that owner's own ``user_session`` (ADR 0004 section 1's two
   steps).

Everything else about the job is then read under RLS as the owner (``classbeat.jobs.runner``).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth, connections
from classbeat.context import UserContext, issuer_token
from classbeat.db import system_session
from classbeat.jobs.fence import Lease

Factory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Claimed:
    """One row of ``queue_claim`` (or ``queue_unsettled``)."""

    job_id: UUID
    owner_id: UUID
    lease_version: int


@dataclass(frozen=True, slots=True)
class Issued:
    lease: Lease
    active: bool  # False: the owner is gone or disabled; the lease's context has no connections


def owner_context(owner_id: UUID) -> UserContext:
    """A bare context for the owner (role ``user``, nothing else): for the account-disabled
    failure and the credit settle, which need only the owner's own rows."""
    return UserContext(user_id=owner_id, role="user", issuer=issuer_token())


async def issue(factory: Factory, claimed: Claimed) -> Issued:
    async with system_session(factory) as db:
        active = await auth.owner_active(db, claimed.owner_id)
    bare = owner_context(claimed.owner_id)
    if not active:
        return Issued(Lease(claimed.job_id, claimed.lease_version, bare), active=False)
    refs = await connections.connection_refs(factory, bare)
    ctx = UserContext(
        user_id=claimed.owner_id,
        role="user",
        entitlements=frozenset(),
        connections=refs,
        issuer=issuer_token(),
    )
    return Issued(Lease(claimed.job_id, claimed.lease_version, ctx), active=True)
