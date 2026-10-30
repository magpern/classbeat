"""What a job asks ``perform`` to write (ADR 0005 section 7, ADR 0007 sections 1, 6 and 8):
``OperationRequest`` with its connection ``Guard``, and the SQL for ``actionable_at``, the time
before which a stale multi-request call may still be sending requests.

Kept apart from ``classbeat.operations.protocol``, which uses both, so each module stays small.
"""

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.operations.kinds import (
    ATTACH_KIND,
    BOUND_KEY,
    CALL_MAY_RUN_MARGIN_SECONDS,
    CLASS_WRITE_KINDS,
    FALLBACK_BOUND_SECONDS,
    MAX_BOUND_SECONDS,
    MAX_CONTEXT_BYTES,
    MULTI_REQUEST_KINDS,
    REF_KEY,
    SPOTIFY_CREATE_KIND,
    SPOTIFY_WRITE_KINDS,
    context_size,
    ref_of,
    spotify_purpose,
)

# ADR 0007 section 8: until when a stale multi-request call may still be sending requests. The
# one definition of ``actionable_at`` (the protocol, the user's actions, the operation view and
# the class delete all use it). NULL when nothing waits: the current attempt's call returned, or
# the row is no multi-request write and has no bound (the attach, the Spotify create). A
# multi-request write (``MULTI_REQUEST_KINDS``: the MOWL class writes and ``spotify_replace_items``)
# whose context carries no usable bound (the protocol never writes one) waits
# ``FALLBACK_BOUND_SECONDS``: a row of unknown bound is never actionable early.
# ``pending_at`` is set by every transition into ``pending``; ``updated_at`` (later) only stands
# in for a row that predates it.
_MULTI_KINDS_SQL: Final = ", ".join(f"'{name}'" for name in sorted(MULTI_REQUEST_KINDS))
_HAS_BOUND_SQL: Final = "jsonb_typeof(request_context -> 'bound_seconds') = 'number'"
# The stored bound, clamped to 0..MAX_BOUND_SECONDS: a context written by anything but the
# protocol can't make a row wait forever or end its wait before it began. Clamped as ``numeric``
# (which holds any jsonb number) before the ``float8`` cast, so ``1e400`` clamps, not errors.
_CLAMPED_BOUND_SQL: Final = (
    f"least(greatest((request_context ->> 'bound_seconds')::numeric, 0), {MAX_BOUND_SECONDS})"
    "::float8"
)
WAITING_SQL: Final = (
    f"(({_HAS_BOUND_SQL} OR kind IN ({_MULTI_KINDS_SQL})) "
    "AND NOT coalesce(call_ended_at >= pending_at, false))"
)
ACTIONABLE_AT_SQL: Final = (
    f"CASE WHEN {WAITING_SQL} THEN coalesce(pending_at, updated_at) + make_interval(secs => "
    f"CASE WHEN {_HAS_BOUND_SQL} THEN {_CLAMPED_BOUND_SQL} "
    f"ELSE {FALLBACK_BOUND_SECONDS} END + {CALL_MAY_RUN_MARGIN_SECONDS}) END"
)
# True while a stale call may still be running: no reconciliation, no user action.
NOT_ACTIONABLE_SQL: Final = f"coalesce(({ACTIONABLE_AT_SQL}) > now(), false)"


# Stands in for the row's id when the context's size is checked before the row exists.
_SIZE_PROBE: Final = UUID(int=0)

# Runs first in step 1's transaction (before the operation row is locked): False ends the job
# ``not_connected`` before any row is written. The attach share-locks the MOWL connection it
# opened (``classbeat.connections.hold``), so a disconnect or reconnect in between cannot let a
# call go out with the old account's credentials.
Guard = Callable[[AsyncSession], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """One write: its kind, its deterministic key and request hash (built by the kind's module),
    the call's timeout (the lease is extended past it) and the connection ``guard``; for a
    class write also its ``class_id``, the ``context`` its reconciler and ``on_succeeded`` need,
    and ``bound_seconds``."""

    kind: str
    idempotency_key: str
    request_hash: bytes
    timeout_seconds: float
    guard: Guard | None = field(default=None, compare=False)
    # The call is exactly one HTTP request: a refusal of that request before it left the
    # process proves no effect. For any other call an allowlist refusal is ``uncertain``.
    # Only for a ``urllib`` call through ``classbeat.egress``'s opener: its redirect handler
    # sets ``OutboundBlocked.after_request``, and an httpx-based call has no such guarantee, so
    # it must leave this False (#117 item 5; M3b).
    single_request: bool = False
    # The class the write belongs to: set exactly for the class writes (the table's check).
    class_id: UUID | None = None
    # Non-secret JSON built by the kind (ids of the user's own rows, hashes, MOWL program ids),
    # stored with this attempt's ``pending`` row; with the bound at most 2048 bytes.
    context: Mapping[str, Any] = field(default_factory=dict[str, Any], compare=False)
    # A multi-request write's total bound (ADR 0007 section 6): the lease is extended by it, it
    # is stored in the context, and a stale call is waited for until ``pending_at + bound + 30 s``.
    bound_seconds: float | None = None
    # The protocol stores ``ref`` (12 hex characters of the row's own id, ``kinds.ref_of``) in
    # the context of every attempt and hands the row's id to the call (``perform_for``), so the
    # write can carry it (the Spotify create's description, ADR 0008 section 2).
    with_ref: bool = False

    def __post_init__(self) -> None:
        if not 1 <= len(self.idempotency_key) <= 120:  # the table's check
            raise ValueError("an operation key is 1 to 120 characters")
        if len(self.request_hash) != 32:
            raise ValueError("a request hash is SHA-256")
        if self.timeout_seconds <= 0:
            raise ValueError("a provider call needs a positive timeout")
        self._check_class()
        if self.bound_seconds is not None:
            if self.single_request:
                raise ValueError("a bound is for a call of several requests")
            if not math.isfinite(self.bound_seconds):
                raise ValueError("a bound is a finite number of seconds")
            if not self.timeout_seconds <= self.bound_seconds:
                raise ValueError("the bound covers the call's whole timeout")
            # Reads clamp a stored bound to this; a larger one would let a row become
            # actionable while its call may still be sending requests.
            if self.bound_seconds > MAX_BOUND_SECONDS:
                raise ValueError("a bound is at most MAX_BOUND_SECONDS")
        elif self.kind in MULTI_REQUEST_KINDS:
            raise ValueError("a write of several requests needs its bound")
        if self.kind == SPOTIFY_CREATE_KIND and not (self.single_request and self.with_ref):
            raise ValueError("a Spotify create is one request and carries its ref")
        self.stored_context()

    def _check_class(self) -> None:
        """A row carries ``class_id`` exactly when its kind is a class write (ADR 0008 section
        1): the MOWL saves and the final-playlist writes. No table check ties a Spotify row's
        class to its purpose, so it is enforced here, from the key."""
        if self.kind == ATTACH_KIND and self.class_id is not None:
            raise ValueError("an attach belongs to no class")
        if self.kind in CLASS_WRITE_KINDS and self.class_id is None:
            raise ValueError("a class write names its class")
        if self.kind in SPOTIFY_WRITE_KINDS:
            purpose = spotify_purpose(self.kind, self.idempotency_key)
            if purpose is None:
                raise ValueError("not a Spotify write's key")
            if (purpose == "final") != (self.class_id is not None):
                raise ValueError("a final-playlist write names its class and a pool write none")
            if self.context.get("purpose") != purpose:
                raise ValueError("a Spotify write's context names the key's purpose")

    def stored_context(self, operation_id: UUID | None = None) -> dict[str, Any]:
        """The ``request_context`` this attempt stores: the kind's context plus the bound (and,
        ``with_ref``, the ``ref`` of the row ``operation_id``), checked again on every use (the
        caller's mapping is not copied at construction)."""
        if BOUND_KEY in self.context or REF_KEY in self.context:
            raise ValueError("the bound and the ref are stored by the protocol itself")
        stored = dict(self.context)
        if self.bound_seconds is not None:
            stored[BOUND_KEY] = self.bound_seconds
        if self.with_ref:
            stored[REF_KEY] = ref_of(operation_id if operation_id is not None else _SIZE_PROBE)
        try:
            size = context_size(stored)
        except (TypeError, ValueError):
            raise ValueError("an operation context is JSON") from None
        if size > MAX_CONTEXT_BYTES:
            raise ValueError("an operation context is at most 2048 bytes")
        return stored

    @property
    def lease_seconds(self) -> float:
        """How long the call may take: the lease outlives it once ``pending`` is written."""
        return max(self.timeout_seconds, self.bound_seconds or 0.0)
