"""The web process's half of the "class ready" mail (ADR 0022 section 4; ME5, #656).

The worker writes a ``mail_handoff`` row in the transaction that finishes a job which ran a fresh
mix analysis (``classbeat.jobs.mail_handoff``) and notifies ``mail_handoff``. ``HandoffConsumer``
runs in the web process:

- **SMTP configured**: woken by ``LISTEN mail_handoff`` (``classbeat.db.listen``, its own
  connection) and by a poll every 30 s, it takes rows with ``mail_handoff_take`` (``SECURITY
  DEFINER``, ``classbeat-queue v4``): ``DELETE ... FOR UPDATE SKIP LOCKED RETURNING``, so two web
  processes never take the same row, and taking a row discards it (fire and forget: one attempt,
  a failure logged by error class only). The same call drops rows older than a day. For each row
  taken: the account must exist and be enabled (its address is read here, never stored in the
  row), the class must still be the owner's (``classes.ready``, in the owner's session), then the
  one send path (``live.send_optional``: the admin switch, the user's preference, the live
  branding and the signed unsubscribe links).
- **SMTP not configured**: no listener; once an hour the take function in its purge-only mode
  (``p_limit`` 0), so the table never grows (guard 10).

Nothing here logs an address, a class id or a row's content: only counts and error classes.
"""

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.classes.ready import still_owned
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.db.listen import listen
from classbeat.emails import live
from classbeat.jobs.mail_handoff import CHANNEL, KIND
from classbeat.mail import Mailer

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]

BATCH: Final = 20
POLL_SECONDS: Final = 30.0
PURGE_SECONDS: Final = 3600.0
STOP_SECONDS: Final = 20.0  # one send's SMTP timeout (15 s) fits

_TAKE = text("SELECT id, owner_id, kind, params FROM public.mail_handoff_take(:limit)")


@dataclass(frozen=True, slots=True)
class Taken:
    """One hand-off row, already deleted from the table."""

    id: UUID
    owner_id: UUID
    kind: str
    params: Mapping[str, Any]


async def take(factory: Factory, limit: int) -> list[Taken]:
    """Take (delete and return) up to ``limit`` rows, oldest first; 0 only purges."""
    async with system_session(factory) as db:
        rows = (await db.execute(_TAKE, {"limit": limit})).all()
    return [Taken(r.id, r.owner_id, r.kind, r.params) for r in rows]


def _class_id(params: Mapping[str, Any]) -> UUID | None:
    try:
        return UUID(str(params.get("class_id")))
    except ValueError:
        return None


async def send_taken(factory: Factory, mailer: Mailer, settings: Settings, row: Taken) -> bool:
    """The mail of one taken row through the one send path; ``True`` if an attempt was made."""
    class_id = _class_id(row.params)
    if row.kind != KIND or class_id is None:
        return False
    async with system_session(factory) as db:
        recipient = await auth.active_email(db, row.owner_id)
    if recipient is None:  # the account is gone or disabled
        return False
    if not await still_owned(factory, auth.mail_handoff_context(row.owner_id), class_id):
        return False
    return await live.send_optional(
        factory,
        mailer,
        settings,
        KIND,
        recipient,
        {"class_id": str(class_id)},
        user_id=row.owner_id,
    )


async def drain(
    factory: Factory, mailer: Mailer, settings: Settings, stop: asyncio.Event | None = None
) -> int:
    """Take and send until the table holds no row younger than a day, or ``stop`` is set (the rows
    already taken are still sent: taking discarded them); returns the mails tried."""
    sent = 0
    while stop is None or not stop.is_set():
        rows = await take(factory, BATCH)
        for index, row in enumerate(rows):
            try:
                sent += await send_taken(factory, mailer, settings, row)
            except asyncio.CancelledError:
                # Shutdown gave up waiting: the rows not yet sent are already deleted (fire and
                # forget), so say how many were lost. A count only, never a row's content.
                logger.warning(
                    "class ready mails lost at shutdown", extra={"count": len(rows) - index}
                )
                raise
            except Exception as exc:  # one row never stops the others; the row is gone
                logger.warning("class ready mail not sent: %s", type(exc).__name__)
        if len(rows) < BATCH:
            break
    return sent


class HandoffConsumer:
    """Started and stopped by the web process's lifespan."""

    def __init__(
        self,
        engine: AsyncEngine,
        factory: Factory,
        mailer: Mailer,
        settings: Settings,
        *,
        poll_seconds: float = POLL_SECONDS,
        purge_seconds: float = PURGE_SECONDS,
    ) -> None:
        self._engine = engine
        self._factory = factory
        self._mailer = mailer
        self._settings = settings
        self._poll = poll_seconds
        self._purge = purge_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Let a take or a send in progress finish (a cancelled query could leave its connection
        open past the engine's dispose), then end; cancelled only if it does not end in time."""
        self._stop.set()
        self._wake.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=STOP_SECONDS)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        self._wake.clear()

    async def _run(self) -> None:
        if not self._mailer.configured:
            await self._purge_only()
            return
        listener = asyncio.create_task(listen(self._engine, self._wake, self._stop, CHANNEL))
        try:
            while not self._stop.is_set():
                try:
                    count = await drain(self._factory, self._mailer, self._settings, self._stop)
                    if count:
                        logger.info("class ready mails tried", extra={"count": count})
                except Exception as exc:  # the database blinked: the next wake or poll retries
                    logger.warning("mail hand-off take failed: %s", type(exc).__name__)
                await self._sleep(self._poll)
        finally:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener

    async def _purge_only(self) -> None:
        while not self._stop.is_set():
            try:
                await take(self._factory, 0)
            except Exception as exc:
                logger.warning("mail hand-off purge failed: %s", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._purge)
