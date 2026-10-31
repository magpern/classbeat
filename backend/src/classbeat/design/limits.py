"""Limits of revising by chat (ADR 0007 section 4).

``RevisionPolicy.limit_for(db, ctx, class_id)`` is the per-tier seam (as the credit ledger of ADR
0005 section 5): ``SettingRevisionPolicy`` is ``CLASSBEAT_REVISIONS_PER_CLASS`` for everyone (the
default here); the web app configures M5's ``classbeat.quota.PlanRevisionPolicy``, the plan's
``revisions_per_class`` under that setting (ADR 0011 section 3). ``ChatLimits`` holds the
two other settings. Web and worker are separate processes: the web app calls ``configure`` when it
is created (``classbeat.web``), the worker takes the context size through ``Runtime``. Without
``configure`` the defaults of the settings apply.
"""

from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.context import UserContext

DEFAULT_REVISIONS: Final = 25
DEFAULT_REQUESTS: Final = 50
DEFAULT_CONTEXT: Final = 10


class RevisionPolicy(Protocol):
    async def limit_for(self, db: AsyncSession, ctx: UserContext, class_id: UUID) -> int: ...


@dataclass(frozen=True, slots=True)
class SettingRevisionPolicy:
    revisions_per_class: int = DEFAULT_REVISIONS

    async def limit_for(self, db: AsyncSession, ctx: UserContext, class_id: UUID) -> int:
        return self.revisions_per_class


@dataclass(frozen=True, slots=True)
class ChatLimits:
    max_requests: int = DEFAULT_REQUESTS
    context_requests: int = DEFAULT_CONTEXT
    policy: RevisionPolicy = SettingRevisionPolicy()


_limits = ChatLimits()


def current() -> ChatLimits:
    return _limits


def configure(settings: Settings | None = None, policy: RevisionPolicy | None = None) -> None:
    """Set the limits from the settings (None: the defaults); ``policy`` replaces the revision
    policy (M5, tests)."""
    global _limits
    if settings is None:
        _limits = ChatLimits(policy=policy or SettingRevisionPolicy())
        return
    _limits = ChatLimits(
        settings.chat_max_requests,
        settings.chat_context_requests,
        policy or SettingRevisionPolicy(settings.revisions_per_class),
    )
