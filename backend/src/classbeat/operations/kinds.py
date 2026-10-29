"""External-operation kinds (ADR 0005 section 7): what each kind adds to the shared protocol.

A kind names its ``Reconciler`` (the read-only check of step 4: did an uncertain write take
effect?) and, optionally, what must be written in the same transaction as a ``succeeded`` row
(``on_succeeded``, for the attach its ``mowl_playlist_links`` row). The key and the request hash
are built by the kind's own module (``classbeat.operations.attach``) and never name the job,
class or connection that asks for the write, so every job of the user on the same target finds
the same row.

M3a registers one kind, ``mowl_attach_spotify_playlist``; M3b adds the MOWL saves, M4 the
Spotify writes. The names are the ``external_operations.kind`` check of migration 0004.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing

Factory = async_sessionmaker[AsyncSession]

# The external_operations.kind check of migration 0004.
KIND_NAMES: Final = frozenset({"mowl_attach_spotify_playlist"})
STATES: Final = ("pending", "succeeded", "failed", "uncertain")
MAX_EXTERNAL_ID = 200  # the external_operations.external_id check


@dataclass(frozen=True, slots=True)
class Operation:
    """One ``external_operations`` row as the code sees it. Never a secret: ``external_id`` is
    the provider's id of the written object and ``result`` is non-secret by the table's rule."""

    id: UUID
    kind: str
    idempotency_key: str
    state: str
    external_id: str | None
    job_id: UUID | None
    result: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """What a reconciler found: ``succeeded`` (the effect is there, with its external id),
    ``failed`` (proof there was none) or ``inconclusive`` (the row stays ``uncertain``)."""

    outcome: Literal["succeeded", "failed", "inconclusive"]
    external_id: str | None = None

    def __post_init__(self) -> None:
        if self.outcome == "succeeded":
            if not self.external_id or len(self.external_id) > MAX_EXTERNAL_ID:
                raise ValueError("a succeeded reconciliation names the external id")
        elif self.external_id is not None:
            raise ValueError("only a succeeded reconciliation names an external id")


INCONCLUSIVE: Final = Reconciliation("inconclusive")


@dataclass(frozen=True, slots=True)
class ReconcileInput:
    """What a reconciler gets. A reconciler that reads the provider opens the user's connection
    itself through ``classbeat.connections`` with ``factory``, ``keyring`` and ``ctx`` (ADR 0004
    section 3); one that needs no read (the attach) ignores them."""

    factory: Factory
    keyring: KeyRing | None
    ctx: UserContext
    operation: Operation


Reconciler = Callable[[ReconcileInput], Awaitable[Reconciliation]]
# (the owner's open transaction, the owner, the operation's key, its external id)
OnSucceeded = Callable[[AsyncSession, UUID, str, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OperationKind:
    name: str
    reconcile: Reconciler
    on_succeeded: OnSucceeded | None = None

    def __post_init__(self) -> None:
        if self.name not in KIND_NAMES:
            raise ValueError("unknown external-operation kind (migration 0004's check)")


class OperationKinds:
    def __init__(self) -> None:
        self._kinds: dict[str, OperationKind] = {}

    def register(self, kind: OperationKind) -> OperationKind:
        if kind.name in self._kinds:
            raise ValueError("this operation kind is registered already")
        self._kinds[kind.name] = kind
        return kind

    def replace(self, kind: OperationKind) -> OperationKind | None:
        """Swap a registered kind (tests use it for a reconciler that finds the effect)."""
        previous = self._kinds.get(kind.name)
        self._kinds[kind.name] = kind
        return previous

    def get(self, name: str) -> OperationKind | None:
        return self._kinds.get(name)

    def require(self, name: str) -> OperationKind:
        kind = self._kinds.get(name)
        if kind is None:
            raise ValueError("unregistered operation kind")
        return kind

    def __iter__(self) -> Iterator[OperationKind]:
        return iter(list(self._kinds.values()))


OPERATION_KINDS = OperationKinds()


def canonical(value: Mapping[str, str]) -> bytes:
    """ADR 0004 section 2's canonical JSON (sorted keys, no spaces, UTF-8), spelled out here
    because only the key-handling modules may import the envelope (the crypto guard); a test
    checks it equals ``classbeat.crypto.envelope.canonical_json``."""
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def request_hash(kind: str, **fields: str) -> bytes:
    """SHA-256 of the canonical JSON of ``{kind, **fields}``: only what the write does, never
    who asks for it."""
    if "kind" in fields:
        raise ValueError("the kind is added by request_hash itself")
    return hashlib.sha256(canonical({"kind": kind, **fields})).digest()
