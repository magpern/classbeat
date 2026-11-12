"""`POST /api/billing/paddle/webhook`: Paddle's signed notifications (ADR 0032 section 3, #973).

Mounted only when `billing_provider` is `fake_paddle` or `paddle` (`classbeat.web`). Anonymous:
the signature is the credential, so the route is on the CSRF and origin allowlist
(`auth.middleware.CSRF_EXEMPT_PATHS`) and exempt from the signed-in general limit, which would
look a session up in the database (`auth.ratelimit.API_EXEMPT_PATHS`).

**Order of work.** The in-memory rate limit (429), the raw body under its cap (413), the
signature and its 5-minute window (401), all before anything is parsed and before any database
access; then `paddle_events.parse` and `refs` (a type that maps to nothing is 200, a malformed
one 422). Then, under the per-process semaphore of 1 (503 when busy, `webhook_gate`):

1. a read-only maint transaction (`webhook_lookup`, no log row): a replay of an event already
   stored is 200; otherwise the `billing_links` row the signed transaction or subscription id
   selects, or 422 `no_link`;
2. one logged write transaction for the link's owner (`webhook_session`): lock the owner, look
   the event up again, check the link is the owner's current subscription (`paddle_links`), map
   it against the stored state and the policy's prices, and hand the event to the writer. A first
   payment then stores the subscription and customer ids on the link; a scheduled change (and a
   newer `subscription.updated` that changes nothing) stores its time on the link.

**Answers** (Paddle retries anything but 2xx): applied, duplicate, ignored and stale: 200;
`out_of_order` (nothing recorded, a retry can land): 409; any other refusal: 422 with its reason
code, counted once per event id (`record_webhook_refusal`); a bad or missing signature: 401.

**Never logged:** the body, the headers, the secret, any id from the payload. A log line names
the status and a fixed reason code only.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.admin.db import AdminDb, record_webhook_refusal, webhook_lookup, webhook_session
from classbeat.auth import bucket_ip, request_ip
from classbeat.billing import links_sql, writer, writer_sql
from classbeat.billing.apply_policy import Applied, Duplicate
from classbeat.billing.errors import BillingRefused
from classbeat.billing.paddle_catalog import catalog_of
from classbeat.billing.paddle_events import map_notification, parse, refs
from classbeat.billing.paddle_links import gate
from classbeat.billing.paddle_paid import STARTS
from classbeat.billing.paddle_signature import HEADER, Rejected, verify
from classbeat.billing.paddle_subscription import data_of
from classbeat.billing.paddle_types import (
    Mapped,
    Notification,
    PaddleProvider,
    Refs,
    Reject,
    Skip,
    Stored,
)
from classbeat.billing.prices import read_prices
from classbeat.billing.provider import ChangeScheduled, Started
from classbeat.billing.webhook_gate import TooLarge, WebhookGate, read_body
from classbeat.config_billing import provider_of, webhook_secrets

PATH: Final = "/api/billing/paddle/webhook"
router = APIRouter(tags=["billing"])
logger = logging.getLogger(__name__)
clock = time.time  # the signature window's clock (a test moves it)

_NOW = text("SELECT now()")


def _answer(status: int, outcome: str, reason: str | None = None) -> JSONResponse:
    logger.info("paddle webhook", extra={"status": status, "outcome": outcome, "reason": reason})
    return JSONResponse({"outcome": outcome, "reason": reason}, status_code=status)


async def _refused(
    db: AdminDb, provider: PaddleProvider, event_id: str, reason: str, status: int
) -> JSONResponse:
    if status == 422:
        await record_webhook_refusal(db, provider, event_id, reason)
    return _answer(status, "refused", reason)


async def _apply(
    session: AsyncSession, provider: PaddleProvider, n: Notification, r: Refs,
    found: links_sql.LinkRow,
) -> JSONResponse:  # fmt: skip
    """Step 2, in the owner's write transaction; a refusal raises `BillingRefused`."""
    owner = found.owner_id
    await writer_sql.lock_owner(session, owner)
    if await writer_sql.event_seen(session, provider, n.event_id):
        return _answer(200, "duplicate")
    now: datetime = (await session.execute(_NOW)).scalar_one()
    state = await writer_sql.load_state(session, owner)
    current = await links_sql.current(session, owner, provider)
    starts = n.event_type == "transaction.completed" and data_of(n).get("origin") in STARTS
    held = gate(
        event_type=n.event_type, starts=starts, link_subscription=found.link.subscription_ref,
        current=None if current is None else current.link.subscription_ref,
    )  # fmt: skip
    if isinstance(held, Reject):
        raise BillingRefused(held.reason, 422)
    if isinstance(held, Skip):
        return _answer(200, "ignored", held.reason)
    prices = await read_prices(session)
    changed_at = None if current is None else current.changed_at
    mapped = map_notification(
        n, owner_id=owner, provider=provider,
        catalog=catalog_of({k: v.offer for k, v in prices.items()}),
        stored=None if state is None else Stored(state, changed_at), link=found.link,
    )  # fmt: skip
    if isinstance(mapped, Reject):
        raise BillingRefused(mapped.reason, 422)
    if isinstance(mapped, Skip):
        if mapped.changed_at is not None and current is not None:
            await links_sql.changed(session, current, mapped.changed_at, now)
        return _answer(200, "ignored", mapped.reason)
    return await _write(session, provider, r, found, current, mapped, now)


async def _write(
    session: AsyncSession, provider: PaddleProvider, r: Refs, found: links_sql.LinkRow,
    current: links_sql.LinkRow | None, mapped: Mapped, now: datetime,
) -> JSONResponse:  # fmt: skip
    event, owner = mapped.event, found.owner_id
    if isinstance(event, Started):
        if r.customer_ref is not None and await links_sql.customer_taken(
            session, provider, r.customer_ref, owner
        ):
            raise BillingRefused("customer_conflict", 422)  # a customer maps to one user
        if await links_sql.subscription_taken(session, provider, mapped.subscription_ref, found.id):
            raise BillingRefused("link_conflict", 422)
    outcome = await writer.apply(session, owner, event, now=now)
    if isinstance(outcome, Duplicate):
        return _answer(200, "duplicate")
    if not isinstance(outcome, Applied):
        return _answer(200, "ignored", outcome.reason)
    if isinstance(event, Started):
        await links_sql.activate(session, found, mapped.subscription_ref, r.customer_ref, now)
    elif isinstance(event, ChangeScheduled) and event.occurred_at is not None and current:
        await links_sql.changed(session, current, event.occurred_at, now)
    return _answer(200, "applied")


async def _process(
    request: Request, db: AdminDb, provider: PaddleProvider, n: Notification, r: Refs
) -> JSONResponse:
    async with webhook_lookup(db) as session:
        if await writer_sql.event_seen(session, provider, n.event_id):
            return _answer(200, "duplicate")
        found = await links_sql.find(session, provider, r)
    if found is None:
        return await _refused(db, provider, n.event_id, "no_link", 422)
    rid = getattr(request.state, "request_id", None) or secrets.token_hex(16)
    try:
        async with webhook_session(db, found.owner_id, str(rid)) as session:
            return await _apply(session, provider, n, r, found)
    except BillingRefused as refused:
        status = 409 if refused.reason == "out_of_order" else 422
        return await _refused(db, provider, n.event_id, refused.reason, status)


@router.post(PATH, include_in_schema=False)
async def receive(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    provider = provider_of(settings)
    keys = webhook_secrets(settings)
    hub: WebhookGate = request.app.state.paddle_webhook
    if provider is None or not keys:
        return _answer(404, "refused", "not_found")  # never mounted so; a second line
    if not hub.limiter.allow(bucket_ip(request_ip(request))):
        return _answer(429, "refused", "rate_limited")
    try:
        body = await read_body(request.stream())
    except TooLarge:
        return _answer(413, "refused", "body_too_large")
    checked = verify(request.headers.get(HEADER), body, keys, now=int(clock()))
    if isinstance(checked, Rejected):
        return _answer(401, "refused", f"signature_{checked.reason}")
    n = parse(body)
    if isinstance(n, Skip):
        return _answer(200, "ignored", n.reason)
    if isinstance(n, Reject):
        return _answer(422, "refused", n.reason)  # no event id to count it by
    db: AdminDb | None = getattr(request.app.state, "admin_db", None)
    if db is None:
        return _answer(503, "refused", "billing_unavailable")
    if not hub.busy.take():
        return _answer(503, "refused", "busy")
    try:
        r = refs(n)
        if isinstance(r, Reject):
            return await _refused(db, provider, n.event_id, r.reason, 422)
        return await _process(request, db, provider, n, r)
    finally:
        hub.busy.release()
