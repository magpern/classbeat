"""The admin area's Settings, flags and plan limits (ADR 0011 sections 4, 6 and 7; AD4 #222), on
the router shell of #210.

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
  ``design`` route whose recorded result covers running (422 ``running_not_evaluated``,
  ``classbeat.models.routes.covers_running``; ADR 0012 amendment of 2026-11-03, issue #465). A
  refusal changes nothing and writes one ``refused`` audit row.
- ``DELETE /settings/{key}``: clears the stored value (the JSON ``null``, which every reader takes
  as no row: maint has no ``DELETE`` on ``app_settings``), so the environment's value applies
  (404 ``not_found`` when nothing was stored).
- ``PATCH /plans/{plan_id}`` ``{classes_per_month?, revisions_per_class?, concurrent_jobs?,
  monthly_cost_micros?, max_class_minutes?, games_allowed?, races_allowed?, ftp_tests_allowed?,
  monthly_price_cents?, intro_price_cents?, intro_price_until?, currency?}``: edits the limits
  within the table's ranges and under the environment's upper bounds (422 ``out_of_range`` or
  ``above_environment_bound``; ``max_class_minutes`` under ``CLASSBEAT_CLASS_MINUTES_MAX``), the
  feature gates (booleans) and the prices (#496). A limit or gate sent as ``null`` is left as it
  is; a price sent as ``null`` is cleared. A price is whole cents from 0 to 10 000 000, the
  introduction price's end a date from 2020 to 2100 (``out_of_range``), the currency three capital
  letters (``invalid_value``); an introduction price without its end date (or the reverse), or a
  price without a currency, once merged with the stored row, is 422 ``price_incomplete``. The next
  request reads the new values; credits already held and jobs already created stay as they are.
  Prices are read and returned here only: no route outside ``/api/admin`` reads them (billing is
  off, ADR 0011 section 8).

The audit ``params`` of a write hold the key and what the value means (the mode, a plan and days,
a banner's level and length, a plan's numbers, gates and currency code), never free text: a
currency that is not three capital letters is left out.
"""

import json
import re
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import auth
from classbeat.admin.audit import Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.flags import core
from classbeat.flags.core import FlagRefused
from classbeat.models.routes import covers_running
from classbeat.plans.entitle import TRIAL_MAX_PLAN, plan_ranks

router = admin_router()

SETTINGS = "/settings"
PLANS = "/plans"

MAX_VALUE_BYTES = 2048  # app_settings_value
HARD_BOUNDS = {  # the plans table's checks (migration 0007)
    "classes_per_month": (0, 10_000),
    "revisions_per_class": (0, 200),
    "concurrent_jobs": (1, 16),
    "monthly_cost_micros": (0, 10_000_000_000),
    "max_class_minutes": (10, 180),  # migration 0026, the brief's own range
}
LIMITS = tuple(HARD_BOUNDS)
GATES = ("games_allowed", "races_allowed", "ftp_tests_allowed")
PRICE_CENTS = ("monthly_price_cents", "intro_price_cents")
PRICES = (*PRICE_CENTS, "intro_price_until", "currency")
PRICE_RANGE = (0, 10_000_000)  # migration 0026
INTRO_UNTIL_RANGE = (date(2020, 1, 1), date(2100, 12, 31))
CURRENCY = re.compile(r"[A-Z]{3}")

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
_PLAN_COLUMNS = (
    "id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs, "
    "monthly_cost_micros, max_class_minutes, games_allowed, races_allowed, ftp_tests_allowed, "
    "monthly_price_cents, intro_price_cents, intro_price_until, currency, features, updated_at, "
    "updated_by"
)
_PLANS = text(f"SELECT {_PLAN_COLUMNS} FROM plans ORDER BY rank")  # noqa: S608
_PLAN_FOR_UPDATE = text(f"SELECT {_PLAN_COLUMNS} FROM plans WHERE id = :p FOR UPDATE")  # noqa: S608
_RANKS = text("SELECT id, rank FROM plans WHERE id = ANY(:ids)")
_ROUTE_KEYS = text("SELECT DISTINCT route_key FROM plans ORDER BY route_key")


def _update_plan(columns: list[str]) -> Any:
    """``UPDATE plans`` of exactly ``columns``, every name one of ``LIMITS``, ``GATES`` or
    ``PRICES`` (constants, never the request's text), each bound to the parameter of its name."""
    if not columns or not set(columns) <= {*LIMITS, *GATES, *PRICES}:
        raise ValueError("an editable plan column")
    assignments = ", ".join(f"{c} = :{c}" for c in columns)
    return text(
        f"UPDATE plans SET {assignments}, updated_at = now(), updated_by = :by "  # noqa: S608
        f"WHERE id = :p RETURNING {_PLAN_COLUMNS}"
    )


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonOut(Out):
    id: UUID
    email: str | None


class FlagOut(Out):
    key: str
    source: str  # environment or database
    effective: Any
    environment: Any
    stored: Any  # the row's value, None when there is none
    stored_valid: bool
    updated_at: datetime | None
    updated_by: PersonOut | None


class PlanOut(Out):
    id: str
    name: str
    rank: int
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    monthly_cost_micros: int
    max_class_minutes: int
    games_allowed: bool
    races_allowed: bool
    ftp_tests_allowed: bool
    # Admin only (billing is off): never in a route outside ``/api/admin``.
    monthly_price_cents: int | None
    intro_price_cents: int | None
    intro_price_until: date | None
    currency: str | None
    features: list[str]
    updated_at: datetime
    updated_by: PersonOut | None


class BoundsOut(Out):
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    monthly_cost_micros: int
    max_class_minutes: int
    trial_days: int


class ReadOnlyOut(Out):
    agent_job_cost_micros: int
    song_job_cost_micros: int
    billing_enabled: bool


class SettingsOut(Out):
    flags: list[FlagOut]
    plans: list[PlanOut]
    bounds: BoundsOut
    read_only: ReadOnlyOut


class SettingIn(In):
    value: Any


class PlanIn(In):
    """Wide schema ranges, so a value the table or the environment refuses reaches the route and
    is refused there with an audit row; anything else (a string for a number, an unknown field)
    is the schema's 422."""

    classes_per_month: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    revisions_per_class: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    concurrent_jobs: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    monthly_cost_micros: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    max_class_minutes: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    games_allowed: StrictBool | None = None
    races_allowed: StrictBool | None = None
    ftp_tests_allowed: StrictBool | None = None
    monthly_price_cents: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    intro_price_cents: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    intro_price_until: date | None = None
    currency: str | None = Field(default=None, max_length=8)

    def asked(self) -> dict[str, Any]:
        """What the edit changes: every limit and gate given a value, and every price named at
        all (``null`` clears a price)."""
        sent = self.model_dump()
        out: dict[str, Any] = {k: sent[k] for k in (*LIMITS, *GATES) if sent[k] is not None}
        out.update({k: sent[k] for k in PRICES if k in self.model_fields_set})
        return out


KeyPath = Annotated[str, Path(pattern=r"^[a-z][a-z_]{0,39}$")]
PlanPath = Annotated[str, Path(pattern=r"^[a-z_]{1,30}$")]


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


def _bounds(settings: Settings) -> dict[str, int]:
    """The most a plan may store: the table's range under the environment's bound (ADR 0011
    section 4: ``CLASSBEAT_JOBS_PER_USER`` is the hard upper bound for every plan)."""
    return {
        "classes_per_month": min(
            HARD_BOUNDS["classes_per_month"][1], settings.classes_per_month_max
        ),
        "revisions_per_class": min(
            HARD_BOUNDS["revisions_per_class"][1], settings.revisions_per_class
        ),
        "concurrent_jobs": min(HARD_BOUNDS["concurrent_jobs"][1], settings.jobs_per_user),
        "monthly_cost_micros": min(
            HARD_BOUNDS["monthly_cost_micros"][1], settings.monthly_cost_micros_max
        ),
        "max_class_minutes": min(HARD_BOUNDS["max_class_minutes"][1], settings.class_minutes_max),
    }


def _person(user_id: UUID | None, emails: dict[UUID, str]) -> PersonOut | None:
    return None if user_id is None else PersonOut(id=user_id, email=emails.get(user_id))


def _plan_out(row: Any, emails: dict[UUID, str]) -> PlanOut:
    return PlanOut(
        id=row.id,
        name=row.name,
        rank=row.rank,
        classes_per_month=row.classes_per_month,
        revisions_per_class=row.revisions_per_class,
        concurrent_jobs=row.concurrent_jobs,
        monthly_cost_micros=row.monthly_cost_micros,
        max_class_minutes=row.max_class_minutes,
        games_allowed=row.games_allowed,
        races_allowed=row.races_allowed,
        ftp_tests_allowed=row.ftp_tests_allowed,
        monthly_price_cents=row.monthly_price_cents,
        intro_price_cents=row.intro_price_cents,
        intro_price_until=row.intro_price_until,
        currency=row.currency,
        features=list(row.features),
        updated_at=row.updated_at,
        updated_by=_person(row.updated_by, emails),
    )


def _stored_valid(key: str, value: Any) -> bool:
    if value is None:
        return True
    try:
        core.validate_for_write(key, value, trial_days_max=10**9)
    except FlagRefused:
        return False
    return True


def _flag(
    key: str,
    settings: Settings,
    row: Any,
    ranks: dict[str, int],
    emails: dict[UUID, str],
) -> FlagOut:
    stored = None if row is None else row.value
    if key == "signup_mode":
        environment: Any = settings.signup_mode
        effective: Any = core.stricter_signup(settings.signup_mode, stored)
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
        effective = "on" if core.running_setting(settings.running_enabled, stored) else "off"
        source = "database" if settings.running_enabled and stored is not None else "environment"
    elif key == "trial":
        environment = {"plan": None, "days": 0}
        trial = core.trial_setting(stored)
        effective = _effective_trial(trial, settings, ranks)
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
        updated_at=None if row is None else row.updated_at,
        updated_by=None if row is None else _person(row.updated_by, emails),
    )


def _effective_trial(
    trial: core.TrialSetting | None, settings: Settings, ranks: dict[str, int]
) -> dict[str, Any] | None:
    """What a trial started now would be, as ``classbeat.plans.trial`` clamps it: the days to
    ``CLASSBEAT_TRIAL_DAYS_MAX``, the plan to at most ``instructor``; ``None`` when off."""
    if trial is None:
        return None
    days = min(trial.days, settings.trial_days_max)
    if days <= 0 or trial.plan not in ranks or TRIAL_MAX_PLAN not in ranks:
        return None
    plan = TRIAL_MAX_PLAN if ranks[trial.plan] > ranks[TRIAL_MAX_PLAN] else trial.plan
    return {"plan": plan, "days": days}


async def _view(session: AsyncSession, settings: Settings) -> SettingsOut:
    rows = {r.key: r for r in (await session.execute(_ALL_SETTINGS)).all()}
    plans = list((await session.execute(_PLANS)).all())
    people = [r.updated_by for r in (*rows.values(), *plans) if r.updated_by is not None]
    emails = await auth.account_emails(session, people)
    ranks = {p.id: p.rank for p in plans}
    return SettingsOut(
        flags=[_flag(k, settings, rows.get(k), ranks, emails) for k in core.KEYS],
        plans=[_plan_out(p, emails) for p in plans],
        bounds=BoundsOut(**_bounds(settings), trial_days=settings.trial_days_max),
        read_only=ReadOnlyOut(
            agent_job_cost_micros=settings.agent_job_cost_micros,
            song_job_cost_micros=settings.song_job_cost_micros,
            billing_enabled=False,  # M7: no billing code exists (ADR 0011 section 8)
        ),
    )


async def _flag_view(session: AsyncSession, settings: Settings, key: str) -> FlagOut:
    (view,) = [f for f in (await _view(session, settings)).flags if f.key == key]
    return view


def _set_params(key: str, value: Any) -> dict[str, Any]:
    """The audit params of a validated write: what it means, never free text."""
    if key in ("signup_mode", "song_picking", "swedebeat_enabled", "running_enabled"):
        return {"key": key, "value": value}
    if key == "trial":
        return {"key": key, "plan": value["plan"], "days": value["days"]}
    return {"key": key, "level": value["level"], "text_length": len(value["text"])}


# --- reads ------------------------------------------------------------------------------------


@router.get(SETTINGS, response_model=SettingsOut)
async def read_settings(request: Request, actor: AdminActor) -> SettingsOut:
    async with admin_session(_db(request), actor, "setting.read") as session:
        return await _view(session, request.app.state.settings)


# --- flag writes ------------------------------------------------------------------------------


@router.put(f"{SETTINGS}/{{key}}", response_model=FlagOut)
async def set_flag(request: Request, actor: AdminActor, key: KeyPath, body: SettingIn) -> FlagOut:
    settings: Settings = request.app.state.settings
    canonical: Any = None
    refusal: str | None = None
    try:
        canonical = core.validate_for_write(key, body.value, trial_days_max=settings.trial_days_max)
        if _stored_bytes(canonical) > MAX_VALUE_BYTES:
            refusal = "invalid_value"
    except FlagRefused as exc:
        refusal = exc.reason
    params: dict[str, Any] = {"key": key} if refusal is not None else _set_params(key, canonical)
    if key == "trial" and refusal is None:
        # Read before the audited transaction (plans is a system table), so a refusal row for a
        # plan that does not exist records only the reason, never the string the admin typed.
        refusal = await _trial_plan_refusal(request, canonical["plan"])
        if refusal == "unknown_plan":
            params = {"key": key}
    async with admin_session(
        _db(request),
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
            await _check_running_evaluated(session)
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


async def _check_running_evaluated(session: AsyncSession) -> None:
    """Every plan's jobs would run running classes on a model whose recorded suite result covers
    running (a ``design`` route that is not stale; never the bootstrap choice). The environment's
    ``CLASSBEAT_RUNNING_ENABLED`` stays the kill switch, and the worker checks each job again."""
    keys = (await session.execute(_ROUTE_KEYS)).scalars().all()
    if not keys or not all([await covers_running(session, k) for k in keys]):
        raise AdminRefused("running_not_evaluated", 422)


@router.delete(f"{SETTINGS}/{{key}}", response_model=FlagOut)
async def clear_flag(request: Request, actor: AdminActor, key: KeyPath) -> FlagOut:
    settings: Settings = request.app.state.settings
    async with admin_session(
        _db(request),
        actor,
        "setting.clear",
        write=True,
        target=Target(kind="setting", id=key),
    ) as session:
        if key not in core.KEYS:
            raise AdminRefused("unknown_key", 422)
        if (await session.execute(_CLEAR, {"k": key, "by": actor.user_id})).first() is None:
            raise AdminRefused("not_found", 404)
        return await _flag_view(session, settings, key)


# --- plan limits ------------------------------------------------------------------------------


@router.patch(f"{PLANS}/{{plan_id}}", response_model=PlanOut)
async def update_plan(
    request: Request, actor: AdminActor, plan_id: PlanPath, body: PlanIn
) -> PlanOut:
    settings: Settings = request.app.state.settings
    asked = body.asked()
    refusal = _plan_refusal(asked, _bounds(settings))
    async with admin_session(
        _db(request),
        actor,
        "plan.update",
        write=True,
        target=Target(kind="plan", id=plan_id),
        params=_plan_params(asked),
    ) as session:
        if refusal is not None:
            raise AdminRefused(refusal, 422)
        current = (await session.execute(_PLAN_FOR_UPDATE, {"p": plan_id})).one_or_none()
        if current is None:
            raise AdminRefused("not_found", 404)
        if not _prices_complete({**current._asdict(), **asked}):
            raise AdminRefused("price_incomplete", 422)
        columns = list(asked)
        row = (
            await session.execute(
                _update_plan(columns), {**asked, "p": plan_id, "by": actor.user_id}
            )
        ).one()
        emails = await auth.account_emails(session, [row.updated_by] if row.updated_by else [])
        return _plan_out(row, emails)


def _plan_params(asked: dict[str, Any]) -> dict[str, Any]:
    """The audit params of a plan edit: numbers, gates, an ISO date and a currency code; a
    currency that is not three capital letters (free text) is left out."""
    params: dict[str, Any] = {}
    cleared = [name for name in PRICES if name in asked and asked[name] is None]
    for name, value in asked.items():
        if value is None or (name == "currency" and not CURRENCY.fullmatch(value)):
            continue
        params[name] = value.isoformat() if isinstance(value, date) else value
    if cleared:
        params["cleared"] = cleared
    return params


def _plan_refusal(asked: dict[str, Any], bounds: dict[str, int]) -> str | None:
    """``out_of_range`` for a value the table refuses, ``above_environment_bound`` for a limit
    within the table's range but over the environment's bound, ``invalid_value`` for a currency
    that is not three capital letters; ``nothing_to_change`` for none."""
    if not asked:
        return "nothing_to_change"
    for name, value in asked.items():
        if name in LIMITS:
            low, high = HARD_BOUNDS[name]
            if not low <= value <= high:
                return "out_of_range"
            if value > bounds[name]:
                return "above_environment_bound"
        elif name in PRICE_CENTS and value is not None:
            if not PRICE_RANGE[0] <= value <= PRICE_RANGE[1]:
                return "out_of_range"
        elif name == "intro_price_until" and value is not None:
            if not INTRO_UNTIL_RANGE[0] <= value <= INTRO_UNTIL_RANGE[1]:
                return "out_of_range"
        elif name == "currency" and value is not None and not CURRENCY.fullmatch(value):
            return "invalid_value"
    return None


def _prices_complete(merged: dict[str, Any]) -> bool:
    """The table's two price checks (migration 0026) on the row as it would be: an introduction
    price comes with its end date, and any price with a currency."""
    if (merged["intro_price_cents"] is None) != (merged["intro_price_until"] is None):
        return False
    priced = merged["monthly_price_cents"] is not None or merged["intro_price_cents"] is not None
    return not priced or merged["currency"] is not None
