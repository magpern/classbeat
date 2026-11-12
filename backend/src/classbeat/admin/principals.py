"""Who a maint transaction runs for, and the log rows that name them (ADR 0011 section 5, ADR 0031
section 3, ADR 0032 section 3). Plain values, no database: the sessions that use them are in
``classbeat.admin.db``, which re-exports every name here (callers import them from there).

Moved out of ``admin/db.py`` (#973, split on touch).
"""

from typing import Any
from uuid import UUID

from classbeat.admin.audit import Target


class Buyer:
    """Who buys, for the billing log (ADR 0031 section 3): the user's id and the request's id.
    Never an ``Actor``: ``admin_audit`` keeps only operators as actors. Built only by the billing
    routes' dependency (``classbeat.billing.routes``; an AST guard checks it) and, for a
    link-resolved notification, inside ``classbeat.admin.db.webhook_session``."""

    __slots__ = ("request_id", "user_id")

    def __init__(self, user_id: UUID, request_id: str) -> None:
        self.user_id, self.request_id = user_id, request_id


class AdminRefused(Exception):
    """An admin action refused for a reason the admin may see (409 ``target_is_admin``, ...):
    ``admin_session`` records it as ``outcome = 'refused'`` and re-raises it. One raised outside
    an ``admin_session`` (an input check before it, ``period_invalid``) is recorded by the
    admin area's handler instead (``classbeat.admin.routes.install``); ``audited`` tells them
    apart, so no refusal gets two rows."""

    def __init__(self, reason: str, status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.audited = False


class AdminSkipped(AdminRefused):
    """A refusal its caller records itself, once for many (the bulk decline's summary row, #994):
    ``admin_session`` rolls back as for any refusal but writes no ``refused`` row and spends none
    of the refusal limiter's budget. Raise it only where the caller writes that summary row."""


class Actor:
    """Who acts, for the audit row: the admin's id and email (at the time), the request's id and
    an HMAC of its IP address. Built only by the admin gate (``classbeat.admin.routes``)."""

    __slots__ = ("email", "ip_hash", "request_id", "user_id")

    def __init__(self, user_id: UUID, email: str, request_id: str, ip_hash: bytes) -> None:
        if len(ip_hash) != 32:
            raise ValueError("ip_hash must be 32 bytes")
        self.user_id, self.email = user_id, email
        self.request_id, self.ip_hash = request_id, ip_hash


def audit_row(
    actor: Actor, action: str, target: Target, params: str, outcome: str, reason: str | None
) -> dict[str, Any]:
    """The parameters of one ``admin_audit`` insert."""
    return {
        "actor_id": actor.user_id,
        "actor_email": actor.email,
        "action": action,
        "target_user_id": target.user_id,
        "target_kind": target.kind,
        "target_id": target.id,
        "params": params,
        "outcome": outcome,
        "reason": reason,
        "request_id": actor.request_id,
        "ip_hash": actor.ip_hash,
    }


def log_row(buyer: Buyer, action: str, outcome: str, reason: str | None) -> dict[str, Any]:
    """The parameters of one ``billing_log`` insert."""
    return {
        "owner_id": buyer.user_id,
        "action": action,
        "outcome": outcome,
        "reason": reason,
        "request_id": buyer.request_id,
    }
