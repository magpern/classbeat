"""``python -m classbeat admin player-subject <email>``'s lookup (ADR 0034 section 1): the
user's pairwise subject for the player's operator (player ADR 0010 section 4). Read-only."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.accounts import AccountError, find_user
from classbeat.auth.oidc.provider import OidcConfigError, load_provider
from classbeat.auth.oidc.subject_calc import pairwise_subject
from classbeat.config import Settings


@dataclass(frozen=True, slots=True)
class PlayerSubject:
    user_id: UUID
    subject: str


async def player_subject(db: AsyncSession, settings: Settings, email: str) -> PlayerSubject:
    try:
        provider = load_provider(settings)
    except OidcConfigError as exc:
        raise AccountError(str(exc)) from None
    if provider is None:
        raise AccountError("the player sign-in is not configured")
    user = await find_user(db, email)
    if user is None:
        raise AccountError("no such user")
    subject = pairwise_subject(provider.pairwise_key, provider.client.client_id, user.id)
    return PlayerSubject(user.id, subject)
