"""``UserContext``: who is acting, carried explicitly (ADR 0002 section 1).

Only ``classbeat.auth`` (from the authenticated server-side session, M1) and
``classbeat.jobs`` (from the job row a worker claimed, M3a) may build one; a guard test
(``tests/test_user_context_guard.py``) refuses any other reference that is not a type annotation.
Everything else receives it as the ``ctx`` parameter.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ROLES = ("user", "admin")


@dataclass(frozen=True, slots=True)
class UserContext:
    """Immutable and hashable. Request- or job-scoped; never stored globally.

    ``connections`` holds ``(provider, credential row id)`` pairs: references to the user's
    credential rows, never the credentials. Use ``connection(provider)`` to look one up.
    """

    user_id: UUID
    role: Literal["user", "admin"]
    entitlements: frozenset[str] = frozenset()
    connections: frozenset[tuple[str, UUID]] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise TypeError("user_id must be a UUID")
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if not isinstance(self.entitlements, frozenset):
            raise TypeError("entitlements must be a frozenset")
        if not isinstance(self.connections, frozenset):
            raise TypeError("connections must be a frozenset of (provider, credential id)")
        providers = [pair[0] for pair in self.connections]
        if len(providers) != len(set(providers)):
            raise ValueError("connections has more than one credential for a provider")

    def connection(self, provider: str) -> UUID | None:
        """The credential row id for ``provider``, or ``None``."""
        for name, credential_id in self.connections:
            if name == provider:
                return credential_id
        return None
