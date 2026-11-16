"""The consent route's SwedeBeat session check (ADR 0036 section 6, SR C6a): before an output choice
is accepted, the gateway's ``GET /auth/session`` with the user's stored bearer, through the one
gateway client seam (``swedebeat_gateway.opener``), with ``SESSION_SECONDS`` as its deadline.

- ``check`` opens the bearer of the user's SwedeBeat connection (``open_bearer``: a refusal is its
  category, nothing sent), requires it to be the connection the choice names (``not_connected``)
  and asks the gateway once. A pass returns the connection's id **and generation**; a failure is a
  reason for the page, and **nothing is written** (the connection row stays as it is: a refused
  bearer is the worker's to confirm, ADR 0035 section 5): ``session_refused`` (a ``401``),
  ``app_misconfigured``, ``rate_limited``, ``upstream_unavailable``, ``invalid_response`` and
  ``outbound_blocked``.
- ``consenting(checked)`` marks, for the create that follows **in the same request**, which
  connection generation was checked; ``outputs_admit`` refuses a ``produce_outputs`` job without
  that mark (``consent_required``), so the jobs API's generic create cannot start one past this
  check, and with a mark of another connection or generation (``connection_changed``). The mark
  is a context variable, reset when the block ends.

Only categories are logged, never the bearer or the key.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from classbeat.connections import ConnectionFailure
from classbeat.connections.swedebeat_account import open_bearer
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked
from classbeat.egress_gateway import SESSION_SECONDS
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener
from classbeat.swedebeat_match import config as match_config

logger = logging.getLogger(__name__)

_FAILED = {"bearer_refused": "session_refused", "redirect": "outbound_blocked"}
_KEPT = frozenset({"app_misconfigured", "rate_limited", "upstream_unavailable"})


@dataclass(frozen=True, slots=True)
class Checked:
    """The SwedeBeat connection whose session the gateway took, as it was then."""

    connection_id: UUID
    generation: int


_consented: ContextVar[Checked | None] = ContextVar("studio_outputs_consent", default=None)


def _reason(category: str) -> str:
    if category in _FAILED:
        return _FAILED[category]
    return category if category in _KEPT else "invalid_response"


async def check(
    factory: Any, keyring: KeyRing, ctx: UserContext, connection_id: UUID
) -> Checked | str:
    """The checked connection, or a reason (see the module docstring)."""
    key = match_config.current().swedebeat_gateway_key
    if key is None:
        return "app_misconfigured"
    try:
        opened = await open_bearer(factory, keyring, ctx)
    except ConnectionFailure as failure:
        return failure.category
    if opened.connection.id != connection_id:
        return "not_connected"
    category: str | None = None
    try:
        async with opener.open_client(SESSION_SECONDS) as client:
            await gateway.session(client, key, opened.bearer)
    except gateway.GatewayFailure as refused:
        category = refused.category
    except OutboundBlocked:
        category = "redirect"
    if category is not None:
        reason = _reason(category)
        logger.info("swedebeat session check failed", extra={"category": reason})
        return reason
    return Checked(opened.connection.id, opened.connection.generation)


@contextmanager
def consenting(checked: Checked) -> Iterator[None]:
    """Mark ``checked`` for the create in this block (see the module docstring)."""
    token = _consented.set(checked)
    try:
        yield
    finally:
        _consented.reset(token)


def consented() -> Checked | None:
    """The connection the route checked for the create running now, if any."""
    return _consented.get()
