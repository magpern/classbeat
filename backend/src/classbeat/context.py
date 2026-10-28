"""``UserContext``: who is acting, carried explicitly (ADR 0002 section 1).

Only ``classbeat.auth`` (from the authenticated server-side session, M1) and
``classbeat.jobs`` (from the job row a worker claimed, M3a) may build one. Two layers say so:

- a runtime barrier: construction needs the issuer token (``issuer_token()``), so
  ``UserContext(...)``, ``type(ctx)(...)``, ``ctx.__class__(...)`` and ``dataclasses.replace``
  without it raise;
- an AST scan (``tests/test_user_context_guard.py``) that refuses any reference to the class,
  the token, ``dataclasses.replace``, ``object.__setattr__`` and similar outside the issuers.

Both stop accidental misuse and make misuse visible in review. Deliberately malicious code
inside our own process (reflection, ``object.__setattr__`` on a frozen instance, ctypes) is out
of scope for runtime checks; the review gate catches it. Everything else receives the context
as the ``ctx`` parameter.
"""

from dataclasses import InitVar, dataclass, field
from typing import Literal
from uuid import UUID

ROLES = ("user", "admin")
_ISSUER = object()  # module-private; handed out only by issuer_token()


def issuer_token() -> object:
    """The token that lets the caller build a ``UserContext``.

    Only ``classbeat.auth`` and ``classbeat.jobs`` may call it (the AST scan refuses any other
    module under ``src/classbeat``); tests use ``tests/ctx.py``.
    """
    return _ISSUER


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
    # Not stored. Without the token, construction fails, and so does ``dataclasses.replace``
    # (it does not pass the token on).
    issuer: InitVar[object] = field(default=None, kw_only=True)

    def __post_init__(self, issuer: object) -> None:
        if issuer is not _ISSUER:
            raise PermissionError("a UserContext can only be issued by classbeat.auth or jobs")
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
