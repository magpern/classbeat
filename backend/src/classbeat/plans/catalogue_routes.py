"""``GET /api/plans`` (ADR 0031, M7a-2): the plans' names, quality and limits for the public
pricing view. No sign-in and no price: the plans' price columns are never named (the scan of
``tests/test_plan_gates.py``) and the app role cannot read them (migration 0030). Read-only, so it
changes nothing for an anonymous caller."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from classbeat.db import system_session
from classbeat.plans.catalogue import plan_catalogue
from classbeat.plans.models import GatesOut, LimitsOut
from classbeat.quota.bounds import Bounds

router = APIRouter(prefix="/api/plans", tags=["plan"])


class CataloguePlanOut(BaseModel):
    id: str
    name: str
    rank: int
    quality: str
    limits: LimitsOut
    gates: GatesOut


class CatalogueOut(BaseModel):
    plans: list[CataloguePlanOut]


@router.get("", response_model=CatalogueOut)
async def get_plans(request: Request, response: Response) -> CatalogueOut:
    response.headers["Cache-Control"] = "no-store"
    bounds = Bounds.from_settings(request.app.state.settings)
    async with system_session(request.app.state.session_factory) as db:
        found = await plan_catalogue(db, bounds)
    return CatalogueOut(
        plans=[
            CataloguePlanOut(
                id=plan.id,
                name=plan.name,
                rank=plan.rank,
                quality=plan.quality,
                limits=LimitsOut(
                    classes_per_month=plan.classes_per_month,
                    revisions_per_class=plan.revisions_per_class,
                    concurrent_jobs=plan.concurrent_jobs,
                    max_class_minutes=plan.max_class_minutes,
                ),
                gates=GatesOut(games=plan.games, races=plan.races, ftp_tests=plan.ftp_tests),
            )
            for plan in found
        ]
    )
