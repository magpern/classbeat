"""The ID token's claims and the discovery document (ADR 0034 section 1). Pure: no I/O.

The claims are exactly ``iss``, ``aud`` (the client id), ``azp``, ``sub``, ``iat``, ``exp`` (5
minutes), ``auth_time``, ``nonce``, ``email`` and ``email_verified``. The discovery document is
pinned field for field; there is no user-info endpoint, no refresh token and no
``offline_access``.
"""

from datetime import datetime, timedelta
from typing import Any

ID_TOKEN_LIFETIME = timedelta(minutes=5)
ACCESS_TOKEN_SECONDS = 300
CLAIMS = ("iss", "aud", "azp", "sub", "iat", "exp", "auth_time", "nonce", "email", "email_verified")


def id_token_claims(
    *,
    issuer: str,
    client_id: str,
    subject: str,
    now: datetime,
    auth_time: datetime,
    nonce: str,
    email: str,
    email_verified: bool,
) -> dict[str, Any]:
    issued = int(now.timestamp())
    return {
        "iss": issuer,
        "aud": client_id,
        "azp": client_id,
        "sub": subject,
        "iat": issued,
        "exp": issued + int(ID_TOKEN_LIFETIME.total_seconds()),
        "auth_time": int(auth_time.timestamp()),
        "nonce": nonce,
        "email": email,
        "email_verified": email_verified,
    }


def discovery(issuer: str) -> dict[str, Any]:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oidc/authorize",
        "token_endpoint": f"{issuer}/oidc/token",
        "jwks_uri": f"{issuer}/oidc/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["ES256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "email"],
        "claims_supported": list(CLAIMS),
        "backchannel_logout_supported": True,
        "backchannel_logout_session_supported": False,
    }
