"""Model routes and the enable gate (ADR 0012 section 5).

A **route** says which provider, model and effort a ``route_key`` (M5 maps a plan to one; until
then every job has ``"default"``) uses for a stage group. Plain async functions on the caller's
open session, so M5's admin calls them inside its own request transaction and writes its audit
row with them; nothing here commits.

- ``set_route`` refuses (``RouteRefused`` with a reason code, which M5 answers as 409) unless the
  model is in the catalog with that effort, its ``data_policy`` is ``allowed`` (OpenAI and
  DeepSeek ship ``owner_pending``), DeepSeek only with ``CLASSBEAT_DEEPSEEK_ENABLED``, and the
  **newest** recorded result for that provider, model, effort and group with the current
  ``suite_version`` passed, was recorded on the running suite (its ``manifest_digest``), and has
  the running code's prompt versions of that group's stages. It then upserts the route at that
  result (the foreign key checks the rest). It **never reads a key file**: M5 calls it from the
  web process, which must never see a model key (ADR 0012 §2).
- ``resolve_route`` (the worker, at the start of every model stage) answers the route's choice
  after checking again what can change without a new result: the catalog entry and effort, the
  provider's key file, ``data_policy: allowed`` for a non-Anthropic provider and the DeepSeek
  setting. A route failing one is **unusable**: ``RouteUnusable`` (a ``JobFailure`` of category
  ``model_config``, before any request; no fallback). With no route the answer is the bootstrap
  choice: ``anthropic`` with ``CLASSBEAT_AGENT_MODEL`` and ``_EFFORT``.
- **Stale** (a route keeps working, flagged): the result's prompt versions are not the running
  code's (``prompt_versions``), its ``suite_version`` is not the current one (``suite_version``),
  its ``manifest_digest`` is not the running suite's (``manifest_digest``, #215), or a newer
  result for the same provider, model, effort and group failed (``newer_failure``).
  ``resolve_route`` logs each (route key, group, reason) once per process ("model route stale",
  no data) and answers the choice with its reasons.
- "Newest" is ``recorded_at``, then the insertion order ``seq`` (``store``), so it is
  deterministic for results recorded in one transaction.

``provider_for_choice`` (building the provider) lives in ``classbeat.models.providers``, which
only the worker imports: this module never loads a provider adapter or a model SDK.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Final, Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import catalog
from classbeat.config import Settings
from classbeat.jobs.errors import JobFailure
from classbeat.jobs.inputs import ROUTE_KEY
from classbeat.models import store
from classbeat.models.store import STAGE_GROUPS, ResultRow, RouteRow
from classbeat.models.versions import (
    group_prompt_versions,
    running_manifest_digest,
    running_prompt_versions,
    running_suite_version,
)

log = logging.getLogger(__name__)

MAX_ACTOR: Final = 200

RefusedReason = Literal[
    "invalid_route_key",
    "invalid_stage_group",
    "invalid_actor",
    "not_in_catalog",
    "effort_not_in_catalog",
    "deepseek_disabled",
    "data_policy",
    "no_result",
    "suite_version",
    "result_failed",
    "manifest_digest",
    "prompt_versions",
    "no_route",
    "foreign_key",
]
UnusableReason = Literal[
    "not_in_catalog", "effort_not_in_catalog", "no_key", "data_policy", "deepseek_disabled"
]
StaleReason = Literal["prompt_versions", "suite_version", "manifest_digest", "newer_failure"]


class RouteRefused(Exception):
    """``set_route`` or ``clear_route`` refused; ``reason`` is a code, never user text."""

    def __init__(self, reason: RefusedReason) -> None:
        super().__init__(reason)
        self.reason: RefusedReason = reason


class RouteUnusable(JobFailure):
    """The route's choice may not be used now; the stage fails ``model_config`` before any
    request is sent. ``reason`` says why (``models list`` shows it, the worker logs it)."""

    def __init__(self, reason: UnusableReason) -> None:
        super().__init__("model_config")
        self.reason: UnusableReason = reason


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """What a stage group runs on. ``route_key`` and ``eval_result_id`` are ``None`` for the
    bootstrap choice (no route)."""

    provider: str
    model: str
    effort: str
    route_key: str | None = None
    eval_result_id: uuid.UUID | None = None
    stale: tuple[StaleReason, ...] = ()


@dataclass(frozen=True, slots=True)
class ResultView:
    """A recorded result as the admin and the CLI show it (no verdict details beyond counts)."""

    id: uuid.UUID
    run_id: uuid.UUID
    recorded_at: str
    suite_version: int
    provider: str
    model: str
    effort: str
    stage_group: str
    passed: bool
    status: str
    total_cost_micros: int
    classbeat_version: str
    authorization_note: str
    current: bool  # current suite_version and the running prompt versions of its group

    @classmethod
    def of(cls, row: ResultRow) -> "ResultView":
        status = row.verdict.get("status")
        return cls(
            id=row.id,
            run_id=row.run_id,
            recorded_at=row.recorded_at.isoformat(),
            suite_version=row.suite_version,
            provider=row.provider,
            model=row.model,
            effort=row.effort,
            stage_group=row.stage_group,
            passed=row.passed,
            status=status if isinstance(status, str) else ("passed" if row.passed else "failed"),
            total_cost_micros=row.total_cost_micros,
            classbeat_version=row.classbeat_version,
            authorization_note=row.authorization_note,
            current=not _stale_without_newer(row),
        )


@dataclass(frozen=True, slots=True)
class RouteView:
    route_key: str
    stage_group: str
    provider: str
    model: str
    effort: str
    set_at: str
    set_by: str
    result: ResultView
    stale: tuple[StaleReason, ...] = field(default=())


def _check_key_and_group(route_key: str, stage_group: str) -> None:
    if not isinstance(route_key, str) or ROUTE_KEY.fullmatch(route_key) is None:
        raise RouteRefused("invalid_route_key")
    if stage_group not in STAGE_GROUPS:
        raise RouteRefused("invalid_stage_group")


def _stale_without_newer(row: ResultRow) -> tuple[StaleReason, ...]:
    reasons: list[StaleReason] = []
    if group_prompt_versions(row.stage_group, row.prompt_versions) != running_prompt_versions(
        row.stage_group
    ):
        reasons.append("prompt_versions")
    if row.suite_version != running_suite_version():
        reasons.append("suite_version")
    if row.manifest_digest != running_manifest_digest():
        reasons.append("manifest_digest")
    return tuple(reasons)


async def staleness(db: AsyncSession, row: ResultRow) -> tuple[StaleReason, ...]:
    """Why a route at ``row`` is stale (empty: it is not)."""
    reasons = list(_stale_without_newer(row))
    if await store.newer_failure(db, row):
        reasons.append("newer_failure")
    return tuple(reasons)


async def gate_result(
    db: AsyncSession,
    route_key: str,
    stage_group: str,
    provider: str,
    model: str,
    effort: str,
    *,
    settings: Settings,
) -> ResultRow:
    """The result ``set_route`` would rely on, or ``RouteRefused`` (see the module). Reads no
    key file."""
    _check_key_and_group(route_key, stage_group)
    entry = catalog.find(provider, model)
    if entry is None:
        raise RouteRefused("not_in_catalog")
    if effort not in entry.efforts:
        raise RouteRefused("effort_not_in_catalog")
    if entry.provider == "deepseek" and not settings.deepseek_enabled:
        raise RouteRefused("deepseek_disabled")
    if entry.data_policy != "allowed":
        raise RouteRefused("data_policy")
    current = running_suite_version()
    newest = await store.newest_result(db, provider, model, effort, stage_group, current)
    if newest is None:
        any_result = await store.newest_result(db, provider, model, effort, stage_group)
        raise RouteRefused("no_result" if any_result is None else "suite_version")
    if not newest.passed:
        raise RouteRefused("result_failed")
    if newest.manifest_digest != running_manifest_digest():
        raise RouteRefused("manifest_digest")
    recorded = group_prompt_versions(stage_group, newest.prompt_versions)
    if recorded != running_prompt_versions(stage_group):
        raise RouteRefused("prompt_versions")
    return newest


async def set_route(
    db: AsyncSession,
    route_key: str,
    stage_group: str,
    provider: str,
    model: str,
    effort: str,
    actor: str,
    *,
    settings: Settings,
) -> RouteRow:
    """Enable ``provider``/``model``/``effort`` for ``route_key`` and ``stage_group`` at the
    newest passing result (see the module); inside the caller's transaction."""
    if not isinstance(actor, str) or not 1 <= len(actor) <= MAX_ACTOR:
        raise RouteRefused("invalid_actor")
    result = await gate_result(
        db, route_key, stage_group, provider, model, effort, settings=settings
    )
    try:
        async with db.begin_nested():
            return await store.upsert_route(db, route_key, stage_group, result, actor)
    except IntegrityError:  # the database's gate (the composite foreign key, the CHECKs)
        raise RouteRefused("foreign_key") from None


async def clear_route(db: AsyncSession, route_key: str, stage_group: str, actor: str) -> None:
    """Remove the route (that stage group of ``route_key`` falls back to the bootstrap choice).
    ``RouteRefused('no_route')`` when there is none. ``actor`` is for the caller's audit row; it
    is not logged."""
    _check_key_and_group(route_key, stage_group)
    if not isinstance(actor, str) or not 1 <= len(actor) <= MAX_ACTOR:
        raise RouteRefused("invalid_actor")
    if not await store.delete_route(db, route_key, stage_group):
        raise RouteRefused("no_route")


async def list_results(
    db: AsyncSession, provider: str | None = None, model: str | None = None
) -> list[ResultView]:
    """The recorded results, newest first."""
    return [ResultView.of(row) for row in await store.results(db, provider, model)]


async def list_routes(db: AsyncSession) -> list[RouteView]:
    """Every route with the result it stands on, its cost and why it is stale (if it is)."""
    views: list[RouteView] = []
    for r in await store.routes(db):
        row = await store.result_of(db, r.eval_result_id)
        views.append(
            RouteView(
                route_key=r.route_key,
                stage_group=r.stage_group,
                provider=r.provider,
                model=r.model,
                effort=r.effort,
                set_at=r.set_at.isoformat(),
                set_by=r.set_by,
                result=ResultView.of(row),
                stale=await staleness(db, row),
            )
        )
    return views


def unusable_reason(
    settings: Settings, provider: str, model: str, effort: str
) -> UnusableReason | None:
    """Why the worker may not use this choice from a route now (``None``: it may). Reads the
    key file, so it is for the worker only (``resolve_route``, ``models list``)."""
    entry = catalog.find(provider, model)
    if entry is None:
        return "not_in_catalog"
    if effort not in entry.efforts:
        return "effort_not_in_catalog"
    if settings.model_key(provider) is None:
        return "no_key"
    if provider != "anthropic" and entry.data_policy != "allowed":
        return "data_policy"
    if provider == "deepseek" and not settings.deepseek_enabled:
        return "deepseek_disabled"
    return None


def bootstrap_choice(settings: Settings) -> ModelChoice:
    """No route: Anthropic with ``CLASSBEAT_AGENT_MODEL`` and ``_EFFORT``, as before M3c. The
    bootstrap can never name another provider."""
    return ModelChoice("anthropic", settings.agent_model, settings.agent_effort)


async def resolve_route(
    db: AsyncSession, route_key: str, stage_group: str, *, settings: Settings
) -> ModelChoice:
    """The choice for a stage group of a job with ``route_key`` (see the module): the route's,
    checked again, or the bootstrap choice without a route. ``RouteUnusable`` (category
    ``model_config``) when the route may not be used; there is no fallback."""
    if not isinstance(route_key, str) or ROUTE_KEY.fullmatch(route_key) is None:
        raise JobFailure("internal")
    if stage_group not in STAGE_GROUPS:
        raise JobFailure("internal")
    found = await store.route(db, route_key, stage_group)
    if found is None:
        return bootstrap_choice(settings)
    reason = unusable_reason(settings, found.provider, found.model, found.effort)
    if reason is not None:
        log.warning(
            "model route unusable",
            extra={"route_key": route_key, "stage_group": stage_group, "reason": reason},
        )
        raise RouteUnusable(reason)
    row = await store.result_of(db, found.eval_result_id)
    stale = await staleness(db, row)
    for why in stale:
        _warn_stale(route_key, stage_group, why)
    return ModelChoice(
        found.provider,
        found.model,
        found.effort,
        route_key=route_key,
        eval_result_id=found.eval_result_id,
        stale=stale,
    )


_warned: set[tuple[str, str, StaleReason]] = set()


def _warn_stale(route_key: str, stage_group: str, reason: StaleReason) -> None:
    """One warning per process for each stale route key, group and reason (ADR 0012 section 5);
    the route keeps working."""
    if (route_key, stage_group, reason) in _warned:
        return
    _warned.add((route_key, stage_group, reason))
    log.warning(
        "model route stale",
        extra={"route_key": route_key, "stage_group": stage_group, "reason": reason},
    )
