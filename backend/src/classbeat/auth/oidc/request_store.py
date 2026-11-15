"""``oidc_requests``: pending authorizations bound to a browser (ADR 0034 section 1).

The browser holds the ``__Host-cb_oidc`` flow cookie (32 random bytes); only its SHA-256 is
stored. A request is found only with its id **and** that hash and before ``expires_at`` (10
minutes); an older fourth request of the same cookie is dropped. ``take_request`` deletes the
row with ``DELETE ... RETURNING`` and the same conditions, so a request issues one code or one
denial, and never one for another browser (amended: the row is kept until then, #1056 N14).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.oidc.request_policy import AuthorizeRequest
from classbeat.auth.oidc.schema import oidc_requests

REQUEST_LIFETIME = timedelta(minutes=10)
PER_BROWSER = 3

_COLUMNS = (
    oidc_requests.c.id,
    oidc_requests.c.redirect_uri,
    oidc_requests.c.state,
    oidc_requests.c.nonce,
    oidc_requests.c.code_challenge,
    oidc_requests.c.prompt_login,
    oidc_requests.c.received_at,
)


@dataclass(frozen=True, slots=True)
class StoredRequest:
    id: UUID
    redirect_uri: str
    state: str
    nonce: str
    code_challenge: str
    prompt_login: bool
    received_at: datetime


async def create_request(db: AsyncSession, browser_hash: bytes, request: AuthorizeRequest) -> UUID:
    request_id: UUID = (
        await db.execute(
            insert(oidc_requests)
            .values(
                browser_hash=browser_hash,
                redirect_uri=request.redirect_uri,
                state=request.state,
                nonce=request.nonce,
                code_challenge=request.code_challenge,
                prompt_login=request.prompt_login,
                expires_at=func.now() + REQUEST_LIFETIME,
            )
            .returning(oidc_requests.c.id)
        )
    ).scalar_one()
    keep = (
        select(oidc_requests.c.id)
        .where(oidc_requests.c.browser_hash == browser_hash)
        .order_by(oidc_requests.c.received_at.desc(), oidc_requests.c.id.desc())
        .limit(PER_BROWSER)
    )
    await db.execute(
        delete(oidc_requests).where(
            oidc_requests.c.browser_hash == browser_hash, oidc_requests.c.id.not_in(keep)
        )
    )
    return request_id


def _bound(request_id: UUID, browser_hash: bytes):
    return (
        oidc_requests.c.id == request_id,
        oidc_requests.c.browser_hash == browser_hash,
        oidc_requests.c.expires_at > func.now(),
    )


async def find_request(
    db: AsyncSession, request_id: UUID, browser_hash: bytes
) -> StoredRequest | None:
    row = (await db.execute(select(*_COLUMNS).where(*_bound(request_id, browser_hash)))).first()
    return None if row is None else StoredRequest(**row._asdict())


async def take_request(
    db: AsyncSession, request_id: UUID, browser_hash: bytes
) -> StoredRequest | None:
    row = (
        await db.execute(
            delete(oidc_requests).where(*_bound(request_id, browser_hash)).returning(*_COLUMNS)
        )
    ).first()
    return None if row is None else StoredRequest(**row._asdict())
