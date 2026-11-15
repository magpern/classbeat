"""The worker's delivery of the player events (ADR 0034 section 2): lease, sign, POST, record.

``deliver_once`` takes one due event (``outbox.claim``, its own transaction), signs a **new**
logout token for it with the event's one ``jti`` (so every attempt has a fresh ``iat`` and the
player still sees one event), POSTs it as the form field ``logout_token`` to the player's
``/api/classbeat/events`` with ``player_async_client`` (one host, no redirect, a total deadline,
a size cap), and records the outcome under the same lease in a second transaction: a ``2xx``
deletes the row, anything else is a retry after the policy's backoff, or ``failed`` once the
40 attempts or the 7 days are used up. A worker whose lease was taken over (it outlived the 60
seconds, the reaper requeued the row) changes nothing: the fence refuses its write.

``run_delivery`` (the worker starts it as ``run_player_delivery``, on an engine of its own)
repeats it until ``stop``: each round first runs ``outbox.maintain`` (requeue, fail, purge; also
with delivery off, so an event nobody can deliver still fails visibly after 7 days), then
delivers up to ``BATCH`` events, then waits ``POLL_SECONDS``. A round that fails
(the database went away) is a warning, and the loop carries on with a growing wait.

Logs carry the event's id, kind, attempt and error category: never the ``sub``, the ``jti``, the
token or an answer's body.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.config import Settings
from classbeat.db import create_engine, create_session_factory, system_session
from classbeat.egress import AnswerTooLarge, OutboundBlocked, player_async_client
from classbeat.player import outbox
from classbeat.player.event_policy import (
    EVENTS_PATH,
    FORM_FIELD,
    ErrorCategory,
    EventKind,
    GiveUp,
    after_failure,
    status_category,
)

logger = logging.getLogger("classbeat.player")

BATCH: Final = 20
POLL_SECONDS: Final = 5.0
BACKOFF_MAX_SECONDS: Final = 60.0


class TokenSigner(Protocol):
    """What ``deliver_once`` needs of ``classbeat.auth.EventSigner`` (no import of auth here)."""

    def logout_token(self, *, kind: EventKind, subject: str, jti: str, now: datetime) -> str: ...


type Outcome = Literal["delivered", "retry", "failed", "lost"]


@dataclass(frozen=True, slots=True)
class Delivery:
    """``deliver_once``'s outcome: ``lost`` when the fence refused the write."""

    outcome: Outcome
    error: ErrorCategory | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _jitter() -> float:
    return random.uniform(-1.0, 1.0)  # noqa: S311 - spreads retries, nothing secret


async def _post(
    client_factory: Callable[[], httpx.AsyncClient], token: str
) -> ErrorCategory | None:
    """``None`` when the player answered ``2xx``; else the category. Never raises for an
    answer, a refusal or a network failure."""
    try:
        async with client_factory() as client:
            response = await client.post(EVENTS_PATH, data={FORM_FIELD: token})
    except AnswerTooLarge:
        return "too_large"
    except OutboundBlocked as refused:
        return "redirect" if refused.after_request else "blocked"
    except (TimeoutError, httpx.TimeoutException):
        return "timeout"
    except httpx.HTTPError:
        return "network"
    return status_category(response.status_code)


async def deliver_once(
    factory: async_sessionmaker[AsyncSession],
    signer: TokenSigner,
    worker: str,
    *,
    client_factory: Callable[[], httpx.AsyncClient] = player_async_client,
    now: Callable[[], datetime] = _utcnow,
    jitter: Callable[[], float] = _jitter,
    clock: Callable[[], datetime] | None = None,
) -> Delivery | None:
    """Deliver one due event, or ``None`` when none is due.

    ``now`` is the token's ``iat`` (the worker's clock; the player checks it against its own).
    The retry is scheduled on ``clock``, by default the database's ``now()`` read in the
    transaction that records it: the clock ``claim`` and ``maintain`` compare ``available_at``
    and ``give_up_at`` against, so the two never mix."""
    async with system_session(factory) as db:
        lease = await outbox.claim(db, worker)
    if lease is None:
        return None
    try:
        token = signer.logout_token(kind=lease.kind, subject=lease.sub, jti=lease.jti, now=now())
    except Exception as exc:  # a broken key set: the event waits for a fixed worker, not lost
        # The type only, never a traceback: nothing near the key material reaches the log.
        logger.error(
            "player event not signed",
            extra={"event_id": str(lease.id), "error_type": type(exc).__name__},
        )
        error: ErrorCategory | None = "unsigned"
    else:
        error = await _post(client_factory, token)
    async with system_session(factory) as db:
        if error is None:
            outcome: Outcome = "delivered" if await outbox.delivered(db, lease) else "lost"
        else:
            at = clock() if clock is not None else await outbox.database_now(db)
            step = after_failure(
                attempt=lease.attempt, now=at, give_up_at=lease.give_up_at, jitter=jitter()
            )
            if isinstance(step, GiveUp):
                outcome = "failed" if await outbox.give_up(db, lease, error) else "lost"
            else:
                outcome = "retry" if await outbox.retry(db, lease, step.at, error) else "lost"
    logger.info(
        "player event attempt",
        extra={
            "event_id": str(lease.id),
            "kind": lease.kind,
            "attempt": lease.attempt,
            "outcome": outcome,
            "error": error,
        },
    )
    return Delivery(outcome, error)


async def _round(
    factory: async_sessionmaker[AsyncSession], signer: TokenSigner | None, worker: str
) -> None:
    async with system_session(factory) as db:
        counts = await outbox.maintain(db)
    if any(counts.values()):
        logger.info("player events maintained", extra=counts)
    if signer is None:
        return
    for _ in range(BATCH):
        if await deliver_once(factory, signer, worker) is None:
            return


async def run_delivery(
    factory: async_sessionmaker[AsyncSession],
    signer: TokenSigner | None,
    worker: str,
    stop: asyncio.Event,
    *,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    """Until ``stop``: maintain, deliver (``signer`` ``None``: delivery is off), wait. A round
    that fails (the database is down) is a warning with its error type, as in the job loop, and
    the wait doubles up to ``BACKOFF_MAX_SECONDS`` until a round succeeds again."""
    delay = poll_seconds
    while not stop.is_set():
        try:
            await _round(factory, signer, worker)
        except Exception as exc:
            logger.warning("player event round failed", extra={"error_type": type(exc).__name__})
            delay = min(delay * 2, max(poll_seconds, BACKOFF_MAX_SECONDS))
        else:
            delay = poll_seconds
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)


async def run_player_delivery(
    settings: Settings, signer: TokenSigner | None, worker: str, stop: asyncio.Event
) -> None:
    """The worker's task: ``run_delivery`` on an engine of its own (one connection and one
    spare), disposed at the end. Nothing is opened when ``stop`` is already set."""
    if stop.is_set():
        return
    engine = create_engine(settings, pool_size=1, max_overflow=1)
    try:
        await run_delivery(create_session_factory(engine), signer, worker, stop)
    finally:
        await engine.dispose()
