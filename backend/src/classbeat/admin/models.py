"""The admin area's Model routes (ADR 0011 section 6, AD5 #223; ADR 0012 section 5), on the router
shell of #210.

Every route has ``admin_gate`` (admin role, TOTP within 12 hours, step-up within 5 minutes for
every write, the admin rate limit, ``no-store``; CSRF by the auth middleware) and does its database
work in one ``admin_session`` on ``classbeat_maint``: the audit row first, a read then read only.
The routes are ``classbeat.models.routes`` run inside that transaction (``set_route``,
``clear_route``, ``list_routes``, ``list_results``); this module adds no gate of its own.

- ``GET /models``: the plans with their ``route_key`` (a plan's jobs resolve the routes of that
  key), every route per route key and stage group with the result it stands on and why it is stale
  (``newer_failure`` means the worker refuses it), the recorded results, and the catalog with what
  ``set_route`` would accept (the data policy and the DeepSeek switch; whether a provider has a
  key is the worker's check, which the web process never makes).
- ``PUT /models/routes/{route_key}/{stage_group}`` ``{provider, model, effort}``: ``set_route``
  at the newest passing result. A ``RouteRefused`` (no passing eval, a stale or failed result, a
  provider whose data policy is not ``allowed``, DeepSeek while it is not enabled, ...) is 409
  with its reason code, audited ``refused``, and nothing changes.
- ``DELETE /models/routes/{route_key}/{stage_group}``: ``clear_route``; 404 ``no_route`` when
  there is none.

The audit ``params`` hold the route key, stage group and the provider, model and effort asked for,
each pattern-checked before the audit row (a request that fails the patterns is a 422 that never
reaches the database). A job's own route key is chosen by ``classbeat.quota.route``, never here.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Path, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text

from classbeat.admin.audit import MODEL_PATTERN, Target
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.agent import catalog
from classbeat.config import Settings
from classbeat.models import routes as model_routes
from classbeat.models.routes import RouteRefused

router = admin_router()

MODELS = "/models"
ROUTE = f"{MODELS}/routes/{{route_key}}/{{stage_group}}"

_PLANS = text("SELECT id, name, rank, route_key FROM plans ORDER BY rank")

RouteKeyPath = Annotated[str, Path(pattern=r"^[a-z0-9_]{1,40}$")]
StagePath = Annotated[str, Path(pattern=r"^(design|songs)$")]


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class RouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,29}$")]
    model: Annotated[str, StringConstraints(pattern=MODEL_PATTERN)]
    effort: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,19}$")]


class PlanRouteOut(Out):
    id: str
    name: str
    rank: int
    route_key: str


class ResultOut(Out):
    id: UUID
    run_id: UUID
    recorded_at: datetime
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
    current: bool


class RouteOut(Out):
    route_key: str
    stage_group: str
    provider: str
    model: str
    effort: str
    set_at: datetime
    set_by: str
    stale: list[str]
    refused_by_worker: bool  # a newer result failed: the worker refuses the route
    result: ResultOut


class CatalogOut(Out):
    provider: str
    model: str
    efforts: list[str]
    data_policy: str
    enabled: bool  # set_route would not refuse it for its data policy or the DeepSeek switch


class ModelsOut(Out):
    plans: list[PlanRouteOut]
    routes: list[RouteOut]
    results: list[ResultOut]
    catalog: list[CatalogOut]


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


def _result(view: model_routes.ResultView) -> ResultOut:
    return ResultOut(
        id=view.id,
        run_id=view.run_id,
        recorded_at=datetime.fromisoformat(view.recorded_at),
        suite_version=view.suite_version,
        provider=view.provider,
        model=view.model,
        effort=view.effort,
        stage_group=view.stage_group,
        passed=view.passed,
        status=view.status,
        total_cost_micros=view.total_cost_micros,
        classbeat_version=view.classbeat_version,
        authorization_note=view.authorization_note,
        current=view.current,
    )


def _route(view: model_routes.RouteView) -> RouteOut:
    return RouteOut(
        route_key=view.route_key,
        stage_group=view.stage_group,
        provider=view.provider,
        model=view.model,
        effort=view.effort,
        set_at=datetime.fromisoformat(view.set_at),
        set_by=view.set_by,
        stale=list(view.stale),
        refused_by_worker="newer_failure" in view.stale,
        result=_result(view.result),
    )


def _catalog(settings: Settings) -> list[CatalogOut]:
    return [
        CatalogOut(
            provider=entry.provider,
            model=entry.model,
            efforts=list(entry.efforts),
            data_policy=entry.data_policy,
            enabled=entry.data_policy == "allowed"
            and (entry.provider != "deepseek" or settings.deepseek_enabled),
        )
        for entry in catalog.CATALOG
    ]


def _refusal(exc: RouteRefused) -> AdminRefused:
    """A route refusal as the admin sees it: 409 with the reason code (nothing changed)."""
    return AdminRefused(exc.reason, 404 if exc.reason == "no_route" else 409)


@router.get(MODELS, response_model=ModelsOut)
async def read_models(request: Request, actor: AdminActor) -> ModelsOut:
    settings: Settings = request.app.state.settings
    async with admin_session(_db(request), actor, "model.read") as session:
        plans = (await session.execute(_PLANS)).all()
        return ModelsOut(
            plans=[
                PlanRouteOut(id=p.id, name=p.name, rank=p.rank, route_key=p.route_key)
                for p in plans
            ],
            routes=[_route(v) for v in await model_routes.list_routes(session)],
            results=[_result(v) for v in await model_routes.list_results(session)],
            catalog=_catalog(settings),
        )


@router.put(ROUTE, response_model=RouteOut)
async def set_route(
    request: Request,
    actor: AdminActor,
    route_key: RouteKeyPath,
    stage_group: StagePath,
    body: RouteIn,
) -> RouteOut:
    settings: Settings = request.app.state.settings
    async with admin_session(
        _db(request),
        actor,
        "model.route_set",
        write=True,
        target=Target(kind="model_route", id=f"{route_key}/{stage_group}"),
        params={"route_key": route_key, "stage_group": stage_group, **body.model_dump()},
    ) as session:
        try:
            await model_routes.set_route(
                session,
                route_key,
                stage_group,
                body.provider,
                body.model,
                body.effort,
                actor.email,
                settings=settings,
            )
        except RouteRefused as exc:
            raise _refusal(exc) from None
        (view,) = [
            v
            for v in await model_routes.list_routes(session)
            if (v.route_key, v.stage_group) == (route_key, stage_group)
        ]
        return _route(view)


@router.delete(ROUTE, status_code=204)
async def clear_route(
    request: Request, actor: AdminActor, route_key: RouteKeyPath, stage_group: StagePath
) -> None:
    async with admin_session(
        _db(request),
        actor,
        "model.route_clear",
        write=True,
        target=Target(kind="model_route", id=f"{route_key}/{stage_group}"),
        params={"route_key": route_key, "stage_group": stage_group},
    ) as session:
        try:
            await model_routes.clear_route(session, route_key, stage_group, actor.email)
        except RouteRefused as exc:
            raise _refusal(exc) from None
