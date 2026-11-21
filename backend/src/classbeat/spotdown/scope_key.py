"""The key of ``cache_scope`` (``scope_calc``, ADR 0040 section 7): HKDF of ``app_secret_key``
with ``SCOPE_KEY_INFO`` (``classbeat.auth.derive_key``). The worker scopes its uploads and lookups
with it; the web app computes the same scope when an account is deleted (``scope_outbox``).

Rotating ``app_secret_key`` gives every owner a new scope: the private analyses under the old ones
are then orphaned in spotdown (a later ``DELETE /v2/scopes/{scope}`` of the new scope does not
reach them), so a rotation must delete the old scopes first, the shared studio scope included:
spotdown never purges it and no account deletion reaches it (ADR 0040, amendment "studio-track
analyses are shared", item 5).
"""

from classbeat.auth import derive_key
from classbeat.config import Settings
from classbeat.spotdown.scope_calc import SCOPE_KEY_INFO


def scope_key(settings: Settings) -> bytes | None:
    """The key, or ``None`` without ``app_secret_key``."""
    secret = settings.app_secret_key
    if secret is None or not secret.get_secret_value():
        return None
    return derive_key(secret.get_secret_value().encode(), SCOPE_KEY_INFO)
