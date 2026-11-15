"""What the classbeat-player link takes from ``classbeat.auth`` (ADR 0034 section 3): the start's
rate limit and the binding the link must hold, classbeat's issuer and the user's pairwise
subject (ADR 0034 section 1). The pairwise key never leaves this package. Pure: no I/O.
"""

from dataclasses import dataclass
from uuid import UUID

from classbeat.auth.oidc.provider import Provider
from classbeat.auth.oidc.subject_calc import pairwise_subject
from classbeat.auth.ratelimit import Bucket, Limit

PLAYER_LINK_START_USER = Limit("player-link-start", "account", 10, 3600)


def player_link_start_buckets(user_id: str) -> list[Bucket]:
    """10 link starts per user and hour; every start counts."""
    return [Bucket(PLAYER_LINK_START_USER, user_id)]


@dataclass(frozen=True, slots=True)
class PlayerBinding:
    """The ``account_subject`` a link must carry, and the ``login_hint`` it is started with."""

    issuer: str
    subject: str


def player_binding(provider: object, user_id: UUID) -> PlayerBinding | None:
    """``None`` while the provider is off (``app.state.oidc`` is ``None``)."""
    if not isinstance(provider, Provider):
        return None
    subject = pairwise_subject(provider.pairwise_key, provider.client.client_id, user_id)
    return PlayerBinding(provider.issuer, subject)
