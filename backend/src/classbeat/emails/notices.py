"""Account notices as background tasks (ADR 0022 section 4, ME3).

A route that changed something describes the mail it owes as a ``Notice`` and queues it after
its commit: ``queue(background, request, notice)``. The task goes through the one send path
(``live.send_optional``: admin switch, user preference, live branding), makes one attempt, and a
failure is logged by error class only, so it never undoes the change or reaches the response.
A request that changed nothing builds no ``Notice``.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.emails import live
from classbeat.emails.dispatch import MailPrefs

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Notice:
    """One mail owed for one committed change. ``prefs`` is set only when the account is about
    to be gone (the send path must not look it up). Params never hold a token or password."""

    kind_id: str
    recipient: str
    params: Mapping[str, Any] = field(default_factory=lambda: {})
    prefs: MailPrefs | None = None


def happened(kind_id: str, recipient: str, **params: Any) -> Notice:
    """A notice that names when it happened (now, UTC)."""
    return Notice(kind_id, recipient, {"when": datetime.now(UTC), **params})


async def read_prefs(db: AsyncSession, user_id: UUID) -> MailPrefs:
    """The user's mail preferences, read while the row still exists (account deleted).

    ME4 (#655) adds the columns and reads them here; until then everyone has the defaults."""
    return MailPrefs()


async def send_notice(request_app: Any, notice: Notice) -> None:
    """The background task: never raises, logs the error class only (the text can name the
    recipient)."""
    try:
        state = request_app.state
        await live.send_optional(
            state.session_factory,
            state.mailer,
            state.settings,
            notice.kind_id,
            notice.recipient,
            notice.params,
            prefs=notice.prefs,
        )
    except Exception as exc:
        logger.warning("mail not sent (%s): %s", notice.kind_id, type(exc).__name__)


def queue(background: BackgroundTasks, request: Request, notice: Notice | None) -> None:
    """Queue the notice (if any) to run after the response."""
    if notice is not None:
        background.add_task(send_notice, request.app, notice)
