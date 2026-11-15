"""The account side of the player events (ADR 0034 section 2), for ``classbeat.auth``'s own
paths and the worker.

- ``sessions_revoked``: called by ``sessions.revoke_user_sessions``, the one path that ends all
  (or all other) sessions of a user, which every revocation of ADR 0003 sections 3 and 7 takes
  (password, email and reset changes, role grants and ``admin revoke``, ``admin mfa-reset``, the
  TOTP reset of ``keys wipe``, admin disable, sign out everywhere, TOTP removed or replaced).
  Signing out one session (``revoke_session``) writes nothing.
- ``account_deleted``: called by ``admin_accounts.delete_account``, which the user's own delete
  and the operator's erase both take, before the ``users`` row goes.
- ``load_event_signer``: the worker's signer of the logout tokens (exported, with
  ``EventSignerRefused``).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.oidc.event_subjects import event_subjects
from classbeat.auth.oidc.provider import OidcConfigError, load_signer
from classbeat.auth.oidc.signing import Signer
from classbeat.config import Settings, secrets_dir
from classbeat.player import outbox
from classbeat.player.event_policy import LOGOUT_TYPE, EventKind, logout_claims


async def sessions_revoked(db: AsyncSession, user_id: UUID) -> None:
    await outbox.record_sessions_revoked(db, await event_subjects(db, user_id))


async def account_deleted(db: AsyncSession, user_id: UUID) -> None:
    await outbox.record_account_deleted(db, await event_subjects(db, user_id))


@dataclass(frozen=True, slots=True)
class EventSigner:
    """Signs a logout token for one event. ``repr`` shows the client, never key material."""

    issuer: str
    client_id: str
    signer: Signer

    def __repr__(self) -> str:
        return f"EventSigner(issuer={self.issuer!r}, client_id={self.client_id!r})"

    def logout_token(self, *, kind: EventKind, subject: str, jti: str, now: datetime) -> str:
        claims = logout_claims(
            issuer=self.issuer,
            client_id=self.client_id,
            subject=subject,
            jti=jti,
            kind=kind,
            now=now,
        )
        return self.signer.sign(claims, typ=LOGOUT_TYPE)


class EventSignerRefused(RuntimeError):
    """The worker cannot sign the player events; the message names the setting or the file,
    never a key."""


def load_event_signer(settings: Settings, directory: Path | None = None) -> EventSigner:
    """The signer of the logout tokens, for a worker with ``player_base_url`` set: it needs
    ``oidc_player_client_id`` and the ``oidc_signing_keys`` file, else ``EventSignerRefused``
    (the worker refuses to start)."""
    client_id = settings.oidc_player_client_id
    if client_id is None:
        raise EventSignerRefused("player_base_url needs oidc_player_client_id")
    try:
        signer = load_signer(directory or secrets_dir())
    except OidcConfigError as exc:
        raise EventSignerRefused(str(exc)) from None
    return EventSigner(settings.public_base_url, client_id, signer)
