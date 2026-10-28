"""Test-only way to build a ``UserContext`` (the runtime barrier needs the issuer token)."""

import uuid
from collections.abc import Iterable
from uuid import UUID

from classbeat.context import _ISSUER, UserContext  # pyright: ignore[reportPrivateUsage]


def make_context(
    user_id: UUID | None = None,
    role: str = "user",
    entitlements: Iterable[str] = (),
    connections: Iterable[tuple[str, UUID]] = (),
) -> UserContext:
    return UserContext(
        user_id=user_id or uuid.uuid4(),
        role=role,  # pyright: ignore[reportArgumentType]
        entitlements=frozenset(entitlements),
        connections=frozenset(connections),
        issuer=_ISSUER,
    )
