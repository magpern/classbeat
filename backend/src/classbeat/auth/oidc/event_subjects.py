"""Who gets player events, and as which subject (ADR 0034 section 2).

Only a user whose ``oidc_grants.first_issued_at`` is set has a player account: nobody else's
deletion or revocation leaves classbeat. For each such grant the subject is the pairwise ``sub``
of that client (``subject_calc``). The pairwise key is read from its secret file only when such
a grant exists, and **a missing or malformed key refuses the change** (``OidcConfigError``, the
transaction rolls back): an event is never dropped in silence. The file is read on each call, so
a process that never meets a player user (the dev stack, most CLI runs) needs no key.

``classbeat_maint`` runs this for the admin area's erase and disable; it holds ``SELECT`` on
exactly the three columns read here.
"""

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.oidc.provider import read_pairwise_key
from classbeat.auth.oidc.schema import oidc_grants
from classbeat.auth.oidc.subject_calc import pairwise_subject
from classbeat.config import secrets_dir


async def event_subjects(
    db: AsyncSession, user_id: UUID, directory: Path | None = None
) -> list[str]:
    """The user's pairwise subjects for every client that issued them a token, or ``[]``."""
    clients = (
        (
            await db.execute(
                select(oidc_grants.c.client_id)
                .where(
                    oidc_grants.c.user_id == user_id,
                    oidc_grants.c.first_issued_at.is_not(None),
                )
                .order_by(oidc_grants.c.client_id)
            )
        )
        .scalars()
        .all()
    )
    if not clients:
        return []
    key = read_pairwise_key(directory or secrets_dir())
    return [pairwise_subject(key, client_id, user_id) for client_id in clients]
