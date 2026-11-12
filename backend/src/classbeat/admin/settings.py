"""The admin area's Settings and flags (ADR 0011 sections 4, 6 and 7; AD4 #222), on the router
shell of #210. The plan edit (``PATCH /plans/{plan_id}``) is ``classbeat.admin.plans``.

Every route has ``admin_gate`` (admin role, TOTP within 12 hours, step-up within 5 minutes for
every write, the admin rate limit, ``no-store``; CSRF by the auth middleware) and does its
database work in one ``admin_session`` on ``classbeat_maint``: the audit row first, a read then
read only.

- ``GET /settings``: every flag key with its effective value and where it comes from
  (``environment`` or ``database``), the value stored and whether it validates, the environment's
  own value; the plans with their limits; the environment's upper bounds; the read-only settings
  (per-job ceilings and billing, shown off).
- ``PUT /settings/{key}`` ``{value}``: stores a flag through ``classbeat.flags`` validation. The
  database can tighten the environment, never loosen it, so ``open`` sign-up is refused
  (422 ``environment_only``), as is any value that does not validate (422 ``invalid_value``), a
  trial longer than ``CLASSBEAT_TRIAL_DAYS_MAX`` (422 ``above_environment_bound``), a trial plan
  that is unknown (422 ``unknown_plan``) or ranks above ``instructor`` (422
  ``plan_above_instructor``), a key that is no flag, billing and Swedebeat included (422
  ``unknown_key``), and ``running_enabled`` stored ``on`` while a plan's ``route_key`` has no
  ``design`` route whose recorded result covers running (422 ``running_not_evaluated``; ADR 0012
  amendment of 2026-11-03, issue #465) through ``models.routes.every_plan_covers`` (the web's own
  ``fake`` provider outside prod counts, ``design.music_kind.fake_covers``). Soundless classes have
  no key (``soundless_enabled`` was retired, ADR 0007 amendment of 2026-11-08, #824), so it is
  ``unknown_key`` too. A refusal changes nothing and writes one ``refused`` audit row.
  ``running_enabled``'s effective value is the three conditions (environment, stored ``on``,
  every plan covered), computed on every read like the form's (ADR 0018 amendment of 2026-11-04,
  #509), so the check at store time is not what keeps it safe: it reads the routes without the
  row locks the route writes take, and a route cleared just after it only makes the next read
  off (a job admitted in between ends ``model_config`` in the worker, which checks its model on
  every attempt, before any call).
- ``DELETE /settings/{key}``: clears the stored value (the JSON ``null``, which every reader takes
  as no row: maint has no ``DELETE`` on ``app_settings``), so the environment's value applies
  (404 ``not_found`` when nothing was stored).

The mail settings (``mail_sender_name``, ``mail_support_email``, ``mail_footer`` and
``mail_kinds_disabled``, #653) use the same two routes and are shown by
``classbeat.admin.mail``, not in ``GET /settings``; their audit ``params`` are the key with the
text's length, or the kind ids.

The audit ``params`` of a write hold the key and what the value means (the mode, a plan and days,
a banner's level and length), never free text.
"""

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import Path, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminRefused, admin_session
from classbeat.admin.plan_rules import bounds
from classbeat.admin.plans import all_plans, plan_out
from classbeat.admin.routes import AdminActor, admin_router, require_admin_db
from classbeat.admin.settings_models import (
    BoundsOut,
    FlagOut,
    PersonOut,
    ReadOnlyOut,
    SettingIn,
    SettingsOut,
)
from classbeat.admin.settings_trial import effective_trial
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.design.music_kind import fake_covers
from classbeat.emails.dispatch import DEFAULT_SENDER_NAME
from classbeat.emails.live import config_from, kind_switches
from classbeat.flags import core
from classbeat.flags.effective import environment_signup_held, environment_signup_mode
from classbeat.models.routes import Variant, every_plan_covers
from classbeat.plans.entitle import TRIAL_MAX_PLAN, plan_ranks

router = admin_router()

SETTINGS = "/settings"

MAX_VALUE_BYTES = 2048  # app_settings_value

_ALL_SETTINGS = text("SELECT key, value, updated_at, updated_by FROM app_settings")
_UPSERT = text(
    "INSERT INTO app_settings (key, value, updated_by) VALUES (:k, CAST(:v AS jsonb), :by) "
    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), "
    "updated_by = EXCLUDED.updated_by RETURNING key, value, updated_at, updated_by"
)
# ``classbeat_maint`` may ``INSERT`` and ``UPDATE`` ``app_settings`` but not ``DELETE`` (ADR 0011
# section 5), so clearing a flag stores the JSON ``null``, which every reader takes as no row.
_CLEAR = text(
    "UPDATE app_settings SET value = 'null'::jsonb, updated_at = now(), updated_by = :by "
    "WHERE key = :k AND value <> 'null'::jsonb RETURNING key"
)
_RANKS = text("SELECT id, rank FROM plans WHERE id = ANY(:ids)")

KeyPath = Annotated[str, Path(pattern=r"^[a-z][a-z_]{0,39}$")]


def _person(user_id: UUID | None, emails: dict[UUID, str]) -> PersonOut | None:
    return None if user_id is None else PersonOut(id=user_id, email=emails.get(user_id))


def _stored_valid(key: str, value: Any) -> bool:
    if value is None:
        return True
    try:
        core.validate_for_write(key, value, trial_days_max=10**9, mail_kinds=kind_switches())
    except core.FlagRefused:
        return False
    return True


def _flag(
    key: str,
    settings: Settings,
    row: Any,
    ranks: dict[str, int],
    emails: dict[UUID, str],
    running_covered: bool = False,
) -> FlagOut:
    stored, note = None if row is None else row.value, None
    if key == "signup_mode":
        note = environment_signup_held(settings)  # the flag on, held at invite: say why
        environment: Any = environment_signup_mode(settings)
        effective: Any = core.stricter_signup(environment, stored)
        source = "database" if effective != environment else "environment"
    elif key == "song_picking":
        environment = "on" if settings.song_picking else "off"
        effective = core.song_picking_setting(settings.song_picking, stored)
        source = "database" if settings.song_picking and stored is not None else "environment"
    elif key == "swedebeat_enabled":
        environment = "on" if settings.swedebeat_enabled else "off"
        effective = "on" if core.swedebeat_setting(settings.swedebeat_enabled, stored) else "off"
        source = "database" if settings.swedebeat_enabled and stored is not None else "environment"
    elif key == "running_enabled":
        environment = "on" if settings.running_enabled else "off"
        switch = core.running_switch(settings.running_enabled, stored)
        effective = "on" if switch and running_covered else "off"
        source = "database" if settings.running_enabled and stored is not None else "environment"
    elif key == "trial":
        environment = {"plan": None, "days": 0}
        trial = core.trial_setting(stored)
        effective = effective_trial(trial, settings, ranks)
        source = "database" if effective is not None else "environment"
    else:  # banner
        environment = None
        shown = core.banner_setting(stored)
        effective = None if shown is None else {"text": shown.text, "level": shown.level}
        source = "database" if shown is not None else "environment"
    return FlagOut(
        key=key,
        source=source,
        effective=effective,
        environment=environment,
        stored=stored,
        stored_valid=_stored_valid(key, stored),
        environment_note=note,
        updated_at=None if row is None else row.updated_at,
        updated_by=None if row is None else _person(row.updated_by, emails),
    )


async def _view(session: AsyncSession, settings: Settings) -> SettingsOut:
    rows = {r.key: r for r in (await session.execute(_ALL_SETTINGS)).all()}
    plans = await all_plans(session)
    people = [r.updated_by for r in (*rows.values(), *plans) if r.updated_by is not None]
    emails = await auth.account_emails(session, people)
    ranks = {p.id: p.rank for p in plans}
    running = await _covered(session, settings, "running")
    return SettingsOut(
        flags=[_flag(k, settings, rows.get(k), ranks, emails, running) for k in core.KEYS],
        plans=[plan_out(p, emails) for p in plans],
        bounds=BoundsOut(
            **bounds(settings),
            trial_days=settings.trial_days_max,
            saves_per_hour=settings.saves_per_hour,
            saves_per_day=settings.saves_per_day,
        ),
        read_only=ReadOnlyOut(
            agent_job_cost_micros=settings.agent_job_cost_micros,
            song_job_cost_micros=settings.song_job_cost_micros,
            billing_enabled=settings.billing_enabled,  # always false in M7a (config refuses true)
        ),
    )


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
        stored_valid=_stored_valid(key, stored),
        updated_at=None if row is None else row.updated_at,
        updated_by=None if row is None else _person(row.updated_by, emails),
    )


async def mail_flags(session: AsyncSession, settings: Settings) -> list[FlagOut]:
    """The four mail settings with their effective values, for ``GET /api/admin/mail``."""
    rows = {r.key: r for r in (await session.execute(_ALL_SETTINGS)).all()}
    emails = await auth.account_emails(
        session, [r.updated_by for r in rows.values() if r.updated_by is not None]
    )
    return [_mail_flag(k, settings, rows.get(k), emails) for k in core.MAIL_KEYS]


async def _flag_view(session: AsyncSession, settings: Settings, key: str) -> FlagOut:
    if key in core.MAIL_KEYS:
        (mail,) = [f for f in await mail_flags(session, settings) if f.key == key]
        return mail
    (view,) = [f for f in (await _view(session, settings)).flags if f.key == key]
    return view


def _set_params(key: str, value: Any) -> dict[str, Any]:
    """The audit params of a validated write: what it means, never free text."""
    if key in ("signup_mode", "song_picking", "swedebeat_enabled", "running_enabled"):
        return {"key": key, "value": value}
    if key == "trial":
        return {"key": key, "plan": value["plan"], "days": value["days"]}
    if key == "mail_kinds_disabled":
        return {"key": key, "kinds": list(value)}
    if key in core.MAIL_KEYS:
        return {"key": key, "text_length": len(value)}  # the text stays in app_settings
    return {"key": key, "level": value["level"], "text_length": len(value["text"])}


# --- reads ------------------------------------------------------------------------------------


@router.get(SETTINGS, response_model=SettingsOut)
async def read_settings(request: Request, actor: AdminActor) -> SettingsOut:
    async with admin_session(require_admin_db(request), actor, "setting.read") as session:
        return await _view(session, request.app.state.settings)


# --- flag writes ------------------------------------------------------------------------------


@router.put(f"{SETTINGS}/{{key}}", response_model=FlagOut)
async def set_flag(request: Request, actor: AdminActor, key: KeyPath, body: SettingIn) -> FlagOut:
    settings: Settings = request.app.state.settings
    canonical: Any = None
    refusal: str | None = None
    try:
        canonical = core.validate_for_write(
            key, body.value, trial_days_max=settings.trial_days_max, mail_kinds=kind_switches()
        )
        if _stored_bytes(canonical) > MAX_VALUE_BYTES:
            refusal = "invalid_value"
    except core.FlagRefused as exc:
        refusal = exc.reason
    params: dict[str, Any] = {"key": key} if refusal is not None else _set_params(key, canonical)
    if key == "trial" and refusal is None:
        # Read before the audited transaction (plans is a system table), so a refusal row for a
        # plan that does not exist records only the reason, never the string the admin typed.
        refusal = await _trial_plan_refusal(request, canonical["plan"])
        if refusal == "unknown_plan":
            params = {"key": key}
    async with admin_session(
        require_admin_db(request),
        actor,
        "setting.set",
        write=True,
        target=Target(kind="setting", id=key),
        params=params,
    ) as session:
        if refusal is not None:
            raise AdminRefused(refusal, 422)
        if key == "trial":
            await _check_trial_plan(session, canonical["plan"])
        if key == "running_enabled" and canonical == "on":
            await _check_running_evaluated(session, settings)
        await session.execute(_UPSERT, {"k": key, "v": json.dumps(canonical), "by": actor.user_id})
        return await _flag_view(session, settings, key)


def _stored_bytes(value: Any) -> int:
    """The bytes ``value::text`` takes in ``app_settings`` (the ``app_settings_value`` check):
    UTF-8, as jsonb prints it (a space after each colon and comma)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")).encode())


async def _trial_plan_refusal(request: Request, plan: str) -> str | None:
    async with system_session(request.app.state.session_factory) as db:
        ranks = await plan_ranks(db, plan, TRIAL_MAX_PLAN)
    if plan not in ranks:
        return "unknown_plan"
    return "plan_above_instructor" if ranks[plan] > ranks[TRIAL_MAX_PLAN] else None


async def _check_trial_plan(session: AsyncSession, plan: str) -> None:
    """The trial's plan exists and ranks at most ``instructor`` (ADR 0011 section 4)."""
    ranks = {
        r.id: r.rank for r in (await session.execute(_RANKS, {"ids": [plan, TRIAL_MAX_PLAN]})).all()
    }
    if plan not in ranks:
        raise AdminRefused("unknown_plan", 422)
    if ranks[plan] > ranks[TRIAL_MAX_PLAN]:
        raise AdminRefused("plan_above_instructor", 422)


async def _covered(session: AsyncSession, settings: Settings, variant: Variant) -> bool:
    """Whether every plan's jobs would design ``variant`` classes on a model that covers them now:
    the web's own ``fake`` outside prod, or a covering ``design`` route for every ``route_key``."""
    return fake_covers(settings) or await every_plan_covers(session, variant)


async def _check_running_evaluated(session: AsyncSession, settings: Settings) -> None:
    """``running_enabled`` is stored ``on`` only with coverage (#465). The effective flag checks
    it again on every read (#509), so a plan added or a route cleared later still turns it off;
    ``CLASSBEAT_RUNNING_ENABLED`` stays the kill switch, and the worker checks each job again."""
    if not await _covered(session, settings, "running"):
        raise AdminRefused("running_not_evaluated", 422)


@router.delete(f"{SETTINGS}/{{key}}", response_model=FlagOut)
async def clear_flag(request: Request, actor: AdminActor, key: KeyPath) -> FlagOut:
    settings: Settings = request.app.state.settings
    async with admin_session(
        require_admin_db(request),
        actor,
        "setting.clear",
        write=True,
        target=Target(kind="setting", id=key),
    ) as session:
        if key not in (*core.KEYS, *core.MAIL_KEYS):
            raise AdminRefused("unknown_key", 422)
        if (await session.execute(_CLEAR, {"k": key, "by": actor.user_id})).first() is None:
            raise AdminRefused("not_found", 404)
        return await _flag_view(session, settings, key)
