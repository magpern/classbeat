"""The admin area's mail settings view and the helpers ``GET /settings`` shares with it (ADR 0022
sections 2 and 6, #653). Moved out of ``classbeat.admin.settings`` unchanged (#1185, the
repository's split-on-touch rule).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.settings_models import FlagOut, PersonOut
from classbeat.config import Settings
from classbeat.emails.dispatch import DEFAULT_SENDER_NAME
from classbeat.emails.live import config_from, kind_switches
from classbeat.flags import core

ALL_SETTINGS = text("SELECT key, value, updated_at, updated_by FROM app_settings")


def person(user_id: UUID | None, emails: dict[UUID, str]) -> PersonOut | None:
    return None if user_id is None else PersonOut(id=user_id, email=emails.get(user_id))


def stored_valid(key: str, value: Any) -> bool:
    if value is None:
        return True
    try:
        core.validate_for_write(key, value, trial_days_max=10**9, mail_kinds=kind_switches())
    except core.FlagRefused:
        return False
    return True


def _mail_flag(key: str, settings: Settings, row: Any, emails: dict[UUID, str]) -> FlagOut:
    """A mail setting as the Mail page shows it: what the send path uses and the environment's
    value (``classbeat.emails.live``)."""
    stored = None if row is None else row.value
    config = config_from(settings, {key: stored})
    environment: Any
    effective: Any
    if key == "mail_sender_name":
        environment, effective = DEFAULT_SENDER_NAME, config.branding.sender_name
    elif key == "mail_support_email":
        environment, effective = settings.mail_support_address, config.branding.support_address
    elif key == "mail_footer":
        environment, effective = None, config.branding.footer
    else:  # mail_kinds_disabled
        environment, effective = [], sorted(config.disabled)
    return FlagOut(
        key=key,
        source="database" if effective != environment else "environment",
        effective=effective,
        environment=environment,
        stored=stored,
        stored_valid=stored_valid(key, stored),
        updated_at=None if row is None else row.updated_at,
        updated_by=None if row is None else person(row.updated_by, emails),
    )


async def mail_flags(session: AsyncSession, settings: Settings) -> list[FlagOut]:
    """The four mail settings with their effective values, for ``GET /api/admin/mail``."""
    rows = {r.key: r for r in (await session.execute(ALL_SETTINGS)).all()}
    emails = await auth.account_emails(
        session, [r.updated_by for r in rows.values() if r.updated_by is not None]
    )
    return [_mail_flag(k, settings, rows.get(k), emails) for k in core.MAIL_KEYS]
