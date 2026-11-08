"""The flags as one request sees them: the environment joined with the database rows
(ADR 0011 section 7). Read per request; nothing is cached.

``app_settings`` is a system table: the app role may read it in any session (``SELECT`` only,
the admin area writes it as ``classbeat_maint``).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.flags import core
from classbeat.flags.core import Banner, SignupMode, SongPicking, TrialSetting

_ALL = text("SELECT key, value FROM app_settings")
_ONE = text("SELECT value FROM app_settings WHERE key = :key")


async def _stored(db: AsyncSession, key: str) -> Any:
    return (await db.execute(_ONE, {"key": key})).scalar_one_or_none()


async def stored_all(db: AsyncSession) -> dict[str, Any]:
    return {row.key: row.value for row in (await db.execute(_ALL)).all()}


async def mail_support_address(db: AsyncSession, environment: str) -> str:
    """The support address people are told to write to: the admin's ``mail_support_email`` when it
    is a valid bare address, else the environment's (the same rule as the mail branding, #653)."""
    return core.mail_support_email_setting(await _stored(db, "mail_support_email")) or environment


async def mail_kinds_disabled(db: AsyncSession, can_disable: frozenset[str]) -> frozenset[str]:
    """The kinds of ``can_disable`` the admin switched off (``mail_kinds_disabled``; #653). For
    the worker's "class ready" hand-off (ME5), which may not load the mail registry."""
    return core.mail_kinds_disabled_setting(await _stored(db, "mail_kinds_disabled"), can_disable)


async def signup_mode(db: AsyncSession, environment: SignupMode) -> SignupMode:
    """The stricter of ``CLASSBEAT_SIGNUP_MODE`` and the stored ``signup_mode``."""
    return core.stricter_signup(environment, await _stored(db, "signup_mode"))


async def song_picking(db: AsyncSession, environment_on: bool) -> SongPicking:
    if not environment_on:  # the kill switch: no read needed
        return "off"
    return core.song_picking_setting(environment_on, await _stored(db, "song_picking"))


async def song_picking_admits(db: AsyncSession, ctx: UserContext, environment_on: bool) -> bool:
    """``off`` refuses everyone; ``on`` admits everyone; ``granted`` only users whose effective
    features include ``song_picking``."""
    from classbeat.plans import effective_features  # the plans package reads flags (trial)

    mode = await song_picking(db, environment_on)
    if mode == "off":
        return False
    if mode == "on":
        return True
    return "song_picking" in await effective_features(db, ctx)


async def swedebeat_enabled(db: AsyncSession, environment_on: bool) -> bool:
    """Off while ``CLASSBEAT_SWEDEBEAT_ENABLED`` is off (no read needed); else the stored flag."""
    if not environment_on:
        return False
    return core.swedebeat_setting(environment_on, await _stored(db, "swedebeat_enabled"))


async def running_switch(db: AsyncSession, environment_on: bool) -> bool:
    """``CLASSBEAT_RUNNING_ENABLED`` and a stored ``running_enabled`` of exactly ``on``
    (``core.running_switch``: missing, ``null`` or invalid is off). Not the effective flag:
    ``running_enabled`` also checks coverage."""
    if not environment_on:
        return False
    return core.running_switch(environment_on, await _stored(db, "running_enabled"))


async def running_enabled(db: AsyncSession, environment_on: bool, *, fake_covers: bool) -> bool:
    """The effective ``running_enabled`` (ADR 0018 amendment of 2026-11-04, #509), computed on
    every read: the switch (``running_switch``) **and** every plan's
    ``route_key`` has a ``design`` route that covers running now
    (``models.routes.every_plan_covers``). ``fake_covers`` as for ``soundless_enabled``."""
    if not await running_switch(db, environment_on):
        return False
    if fake_covers:
        return True
    from classbeat.models.routes import every_plan_covers  # see ``soundless_enabled``

    return await every_plan_covers(db, "running")


async def soundless_enabled(db: AsyncSession, environment_on: bool, *, fake_covers: bool) -> bool:
    """The effective ``soundless_enabled`` (ADR 0007 amendment of 2026-11-03, item 2, as amended
    on 2026-11-08, #824), computed on every read: ``CLASSBEAT_SOUNDLESS_ENABLED`` (the kill
    switch, ``environment_on``) **and** every plan's ``route_key`` has a ``design`` route that
    covers soundless now (``models.routes.every_plan_covers``), so a plan added later or a route
    cleared later turns it off without anyone storing anything. There is no stored admin value:
    no ``app_settings`` row is read. ``fake_covers`` is the web's own ``fake`` model provider
    outside prod (``design.music_kind.SoundlessGate``), which counts as covering, as the worker's
    ``fake`` bypass does; it is never true in prod."""
    if not environment_on:  # the kill switch: no read needed
        return False
    if fake_covers:
        return True
    # Imported here: ``classbeat.models`` loads the agent's stage definitions, which the plans
    # package (a reader of this module) must not import at its own import time.
    from classbeat.models.routes import every_plan_covers

    return await every_plan_covers(db, "soundless")


async def trial(db: AsyncSession) -> TrialSetting | None:
    return core.trial_setting(await _stored(db, "trial"))


async def banner(db: AsyncSession) -> Banner | None:
    return core.banner_setting(await _stored(db, "banner"))
