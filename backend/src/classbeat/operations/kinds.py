"""External-operation kinds (ADR 0005 section 7): what each kind adds to the shared protocol.

A kind names its ``Reconciler`` (the read-only check of step 4: did an uncertain write take
effect?) and, optionally, what must be written in the same transaction as a ``succeeded`` row
(``on_succeeded``, for the attach its ``mowl_playlist_links`` row). Both get the ``Operation``,
with the ``context`` the kind stored at the ``pending`` insert (ADR 0007 section 8: the baseline,
hashes and ids a reconciler needs) and its call timestamps. The key and the request hash are built
by the kind's own module (``classbeat.operations.attach``) and never name the connection that asks
for the write; the attach's never name the job or class either, so every job of the user on the
same target finds the same row.

M3a registers ``mowl_attach_spotify_playlist``; M3b adds the MOWL saves (``mowl_save_workout``,
``mowl_update_workout``), M4 the Spotify writes. The names are the ``external_operations.kind``
check of migrations 0004 to 0006.
"""

import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing

Factory = async_sessionmaker[AsyncSession]

ATTACH_KIND: Final = "mowl_attach_spotify_playlist"
# The multi-request MOWL writes of a class (ADR 0007 sections 6 and 7): their rows carry the class.
CLASS_WRITE_KINDS: Final = frozenset({"mowl_save_workout", "mowl_update_workout"})
# The Spotify writes (ADR 0008 section 2; reserved by migration 0006, their code is issue OP1). A
# pool row has no class, a final-playlist row has one.
SPOTIFY_WRITE_KINDS: Final = frozenset({"spotify_create_playlist", "spotify_replace_items"})
# The external_operations.kind check of migrations 0004 to 0006.
KIND_NAMES: Final = frozenset({ATTACH_KIND, *CLASS_WRITE_KINDS, *SPOTIFY_WRITE_KINDS})
STATES: Final = ("pending", "succeeded", "failed", "uncertain")
MAX_EXTERNAL_ID = 200  # the external_operations.external_id check
MAX_CONTEXT_BYTES: Final = 2048  # the external_operations.request_context check
# ADR 0007 section 8: a stale call of a multi-request write may still be sending requests for up
# to its bound; the user's actions and the automatic reconciliation wait this long past it.
CALL_MAY_RUN_MARGIN_SECONDS: Final = 30
# The bound taken for a class write whose stored context has none (``requests.ACTIONABLE_AT_SQL``).
FALLBACK_BOUND_SECONDS: Final = 3600
# The largest bound a request may carry (``OperationRequest`` refuses more); a stored bound is
# clamped to 0..this wherever it is read, for a row the protocol did not write.
MAX_BOUND_SECONDS: Final = 1800
BOUND_KEY: Final = "bound_seconds"  # where the protocol stores the bound in the context


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
    class_id: UUID | None = None
    # What the kind stored with this attempt's ``pending`` row (non-secret, at most 2048 bytes).
    context: Mapping[str, Any] = field(default_factory=dict[str, Any])
    pending_at: datetime | None = None
    call_ended_at: datetime | None = None

    @property
    def call_returned(self) -> bool:
        """The current attempt's call came back (ADR 0007 section 1): ``call_ended_at >=
        pending_at``. A take-over of a ``failed`` row clears ``call_ended_at``, so a timestamp
        of an earlier attempt never counts (the save's listing-lag rule relies on it)."""
        return (
            self.pending_at is not None
            and self.call_ended_at is not None
            and self.call_ended_at >= self.pending_at
        )

    @property
    def bound_seconds(self) -> float | None:
        """The call's total bound for a multi-request write, as stored in the context."""
        return bound_of(self.context)


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
# (the owner's open transaction, the owner, the ``succeeded`` operation with its external id
# and context)
OnSucceeded = Callable[[AsyncSession, UUID, Operation], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OperationKind:
    name: str
    reconcile: Reconciler
    on_succeeded: OnSucceeded | None = None

    def __post_init__(self) -> None:
        if self.name not in KIND_NAMES:
            raise ValueError("unknown external-operation kind (the migrations' check)")


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


def bound_of(context: Mapping[str, Any]) -> float | None:
    value = context.get(BOUND_KEY)
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        return None  # also NaN
    # Clamped before the float conversion, so a huge int (or inf) is MAX_BOUND_SECONDS, not an
    # OverflowError.
    return float(min(value, MAX_BOUND_SECONDS))


def context_size(context: Mapping[str, Any]) -> int:
    """The size the table's check measures (``octet_length(request_context::text)``), counted the
    way jsonb prints a value: ``", "`` and ``": "`` between items and numbers in full (``1e16``
    is ``10000000000000000``). Raises ``ValueError`` on what Postgres refuses (a NUL character
    in a string or key, a non-finite number, text that is not UTF-8) and ``TypeError`` on a
    value JSON can't hold."""
    return len(_jsonb_text(dict(context)).encode("utf-8"))


def _jsonb_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("jsonb holds no NaN or infinity")
        return format(Decimal(repr(value)), "f")  # numeric prints no exponent
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("jsonb holds no NUL character")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        items: list[str] = []
        for key, item in cast(Mapping[Any, Any], value).items():
            if not isinstance(key, str):
                raise TypeError("a JSON key is a string")
            items.append(f"{_jsonb_text(key)}: {_jsonb_text(item)}")
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_jsonb_text(item) for item in cast(list[Any], value)) + "]"
    raise TypeError("not a JSON value")


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
