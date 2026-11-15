"""Sealing and opening one ``connections`` row's secret (ADR 0004 §2, §3).

The envelope of ``classbeat.crypto.envelope``, bound to the row: the AAD names the owner, the
provider and the row id, so a ciphertext moved to another row, user or provider never opens. A
new DEK for every write, under the current KEK. Callers run these inside their own
``user_session`` transaction; nothing here logs or raises a detail.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.crypto.envelope import (
    DecryptFailed,
    connection_aad,
    connection_wrap_aad,
    open_sealed,
    seal,
)
from classbeat.crypto.keks import KeyRing, KeyUnavailable


async def seal_row(
    db: AsyncSession,
    ring: KeyRing,
    owner_id: UUID,
    provider: str,
    row_id: UUID,
    plaintext: bytes,
) -> dict[str, Any]:
    """The secret columns of the row ``row_id``: ciphertext, nonces, wrapped DEK and KEK id."""
    kek = await ring.current(db)
    sealed = seal(
        plaintext,
        connection_aad(owner_id, provider, row_id),
        kek,
        connection_wrap_aad(kek.key_id, owner_id, provider, row_id),
    )
    return {
        "secret": sealed.ciphertext,
        "secret_nonce": sealed.nonce,
        "wrapped_dek": sealed.wrapped_dek,
        "dek_nonce": sealed.dek_nonce,
        "key_id": sealed.key_id,
    }


async def open_row(
    db: AsyncSession, ring: KeyRing, owner_id: UUID, provider: str, row: Any
) -> tuple[str | None, bytes | None]:
    """``(None, plaintext)``, or ``("key_unavailable" | "decrypt_failed", None)``. ``row`` has
    ``id``, ``key_id``, ``secret``, ``secret_nonce``, ``wrapped_dek`` and ``dek_nonce``."""
    try:
        kek = await ring.kek_for(db, row.key_id)
    except KeyUnavailable:
        return "key_unavailable", None
    try:
        plaintext = open_sealed(
            row.secret,
            row.secret_nonce,
            row.wrapped_dek,
            row.dek_nonce,
            kek,
            connection_aad(owner_id, provider, row.id),
            connection_wrap_aad(kek.key_id, owner_id, provider, row.id),
        )
    except DecryptFailed:
        return "decrypt_failed", None
    return None, plaintext
