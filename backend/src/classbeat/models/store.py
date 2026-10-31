"""The SQL of the enable gate (ADR 0012 section 5): ``model_eval_results`` and ``model_routes``.

This is the **only** module under ``backend/src/classbeat`` that names the results table, and
``record_result`` is the only function that writes it; ``classbeat.evals.suite_live`` (EV5) is
the only module that may import ``record_result`` (``tests/test_models_guard.py``). Every
function runs on the caller's open session and never commits: the caller owns the transaction.

What the database itself refuses (migration ``0009_models``): a result whose ``passed`` is not
the JSON boolean ``passed`` of its object verdict, a ``fake`` or unknown provider, any NULL, any
``UPDATE`` or ``DELETE`` of a result by the app role, and a route at anything but a pass for
exactly its provider, model, effort and stage group.
"""

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import catalog

StageGroup = Literal["design", "songs"]
STAGE_GROUPS: Final[tuple[StageGroup, ...]] = ("design", "songs")

_COLUMNS = (
    "id, run_id, recorded_at, suite_version, manifest_digest, provider, model, effort, "
    "stage_group, prompt_versions, classbeat_version, results_sha256, verdict, passed, "
    "total_cost_micros, authorization_note"
)
_INSERT_RESULT = text(
    f"INSERT INTO model_eval_results ({_COLUMNS}) VALUES ("  # noqa: S608 - constant names
    ":id, :run_id, now(), :suite_version, :manifest_digest, :provider, :model, :effort, "
    ":stage_group, CAST(:prompt_versions AS jsonb), :classbeat_version, :results_sha256, "
    "CAST(:verdict AS jsonb), :passed, :total_cost_micros, :authorization_note) "
    "RETURNING recorded_at"
)
_SELECT_RESULTS = f"SELECT {_COLUMNS} FROM model_eval_results"  # noqa: S608
_NEWEST = " ORDER BY recorded_at DESC, id DESC"
_ROUTE_COLUMNS = "route_key, stage_group, provider, model, effort, eval_result_id, set_at, set_by"


class ResultInvalid(ValueError):
    """``record_result`` refused its input; the message names the rule, never the value."""


@dataclass(frozen=True, slots=True)
class ResultRow:
    id: uuid.UUID
    run_id: uuid.UUID
    recorded_at: datetime
    suite_version: int
    manifest_digest: str
    provider: str
    model: str
    effort: str
    stage_group: str
    prompt_versions: dict[str, str]
    classbeat_version: str
    results_sha256: str
    verdict: dict[str, Any]
    passed: bool
    total_cost_micros: int
    authorization_note: str


@dataclass(frozen=True, slots=True)
class RouteRow:
    route_key: str
    stage_group: str
    provider: str
    model: str
    effort: str
    eval_result_id: uuid.UUID
    set_at: datetime
    set_by: str


def _result(row: Any) -> ResultRow:
    return ResultRow(
        id=row.id,
        run_id=row.run_id,
        recorded_at=row.recorded_at,
        suite_version=row.suite_version,
        manifest_digest=row.manifest_digest,
        provider=row.provider,
        model=row.model,
        effort=row.effort,
        stage_group=row.stage_group,
        prompt_versions=dict(row.prompt_versions),
        classbeat_version=row.classbeat_version,
        results_sha256=row.results_sha256,
        verdict=dict(row.verdict),
        passed=row.passed,
        total_cost_micros=row.total_cost_micros,
        authorization_note=row.authorization_note,
    )


def _route(row: Any) -> RouteRow:
    return RouteRow(
        route_key=row.route_key,
        stage_group=row.stage_group,
        provider=row.provider,
        model=row.model,
        effort=row.effort,
        eval_result_id=row.eval_result_id,
        set_at=row.set_at,
        set_by=row.set_by,
    )


def file_sha256(document: bytes) -> str:
    """The SHA-256 of a results file as written (``results_sha256``)."""
    return hashlib.sha256(document).hexdigest()


async def record_result(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    suite_version: int,
    manifest_digest: str,
    provider: str,
    model: str,
    effort: str,
    stage_group: str,
    prompt_versions: Mapping[str, str],
    classbeat_version: str,
    results_sha256: str,
    verdict: Mapping[str, Any],
    total_cost_micros: int,
    authorization_note: str,
) -> ResultRow:
    """Insert one recorded run of one stage group; ``passed`` is the verdict's own JSON boolean.

    For the live command only (``classbeat.evals.suite_live``, after the last run, with the
    verdict it computed from those runs). Refuses a model outside the catalog or an effort the
    entry does not list, so no result names a model classbeat cannot use; the database refuses
    the rest (see the module)."""
    entry = catalog.find(provider, model)
    if entry is None:
        raise ResultInvalid("the provider and model are not in the catalog")
    if effort not in entry.efforts:
        raise ResultInvalid("the catalog entry does not list that effort")
    if stage_group not in STAGE_GROUPS:
        raise ResultInvalid("unknown stage group")
    passed = verdict.get("passed")
    if not isinstance(passed, bool):
        raise ResultInvalid("the verdict's passed is a JSON boolean")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in prompt_versions.items()):
        raise ResultInvalid("prompt_versions maps a stage name to its prompt version")
    result_id = uuid.uuid4()
    params = {
        "id": result_id,
        "run_id": run_id,
        "suite_version": suite_version,
        "manifest_digest": manifest_digest,
        "provider": provider,
        "model": model,
        "effort": effort,
        "stage_group": stage_group,
        "prompt_versions": json.dumps(dict(prompt_versions), sort_keys=True),
        "classbeat_version": classbeat_version,
        "results_sha256": results_sha256,
        "verdict": json.dumps(dict(verdict), sort_keys=True, allow_nan=False),
        "passed": passed,
        "total_cost_micros": total_cost_micros,
        "authorization_note": authorization_note,
    }
    await db.execute(_INSERT_RESULT, params)
    return await result_of(db, result_id)


async def result_by_id(db: AsyncSession, result_id: uuid.UUID) -> ResultRow | None:
    row = (await db.execute(text(_SELECT_RESULTS + " WHERE id = :id"), {"id": result_id})).first()
    return None if row is None else _result(row)


async def result_of(db: AsyncSession, result_id: uuid.UUID) -> ResultRow:
    """A result that must exist (a route's: the foreign key's ``RESTRICT`` keeps it; one just
    inserted in this transaction); ``LookupError`` otherwise."""
    row = await result_by_id(db, result_id)
    if row is None:
        raise LookupError("no such eval result")
    return row


async def results(
    db: AsyncSession, provider: str | None = None, model: str | None = None
) -> list[ResultRow]:
    """Every recorded result, newest first, optionally of one provider and model."""
    where, params = [], {}
    if provider is not None:
        where.append("provider = :provider")
        params["provider"] = provider
    if model is not None:
        where.append("model = :model")
        params["model"] = model
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (await db.execute(text(_SELECT_RESULTS + clause + _NEWEST), params)).all()
    return [_result(r) for r in rows]


async def newest_result(
    db: AsyncSession,
    provider: str,
    model: str,
    effort: str,
    stage_group: str,
    suite_version: int | None = None,
) -> ResultRow | None:
    """The newest result for exactly this provider, model, effort and group (of that suite
    version, when given)."""
    sql = _SELECT_RESULTS + (
        " WHERE provider = :provider AND model = :model AND effort = :effort "
        "AND stage_group = :stage_group"
    )
    params: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "effort": effort,
        "stage_group": stage_group,
    }
    if suite_version is not None:
        sql += " AND suite_version = :suite_version"
        params["suite_version"] = suite_version
    row = (await db.execute(text(sql + _NEWEST + " LIMIT 1"), params)).first()
    return None if row is None else _result(row)


async def newer_failure(db: AsyncSession, result: ResultRow) -> bool:
    """Whether a result recorded after ``result``, for the same provider, model, effort and
    group, failed (a staleness reason, ADR 0012 section 5)."""
    found = await db.execute(
        text(
            "SELECT 1 FROM model_eval_results WHERE provider = :provider AND model = :model "
            "AND effort = :effort AND stage_group = :stage_group AND NOT passed "
            "AND (recorded_at, id) > (:recorded_at, :id) LIMIT 1"
        ),
        {
            "provider": result.provider,
            "model": result.model,
            "effort": result.effort,
            "stage_group": result.stage_group,
            "recorded_at": result.recorded_at,
            "id": result.id,
        },
    )
    return found.first() is not None


async def upsert_route(
    db: AsyncSession, route_key: str, stage_group: str, result: ResultRow, actor: str
) -> RouteRow:
    """Point the route at ``result`` (its provider, model and effort). The foreign key refuses
    a result that is not a pass for exactly those; ``gate_passed`` is always true."""
    row = (
        await db.execute(
            text(
                f"INSERT INTO model_routes ({_ROUTE_COLUMNS}, gate_passed) VALUES ("  # noqa: S608
                ":route_key, :stage_group, :provider, :model, :effort, :eval_result_id, now(), "
                ":set_by, true) ON CONFLICT (route_key, stage_group) DO UPDATE SET "
                "provider = EXCLUDED.provider, model = EXCLUDED.model, effort = EXCLUDED.effort, "
                "eval_result_id = EXCLUDED.eval_result_id, gate_passed = true, "
                f"set_at = EXCLUDED.set_at, set_by = EXCLUDED.set_by RETURNING {_ROUTE_COLUMNS}"
            ),
            {
                "route_key": route_key,
                "stage_group": stage_group,
                "provider": result.provider,
                "model": result.model,
                "effort": result.effort,
                "eval_result_id": result.id,
                "set_by": actor,
            },
        )
    ).one()
    return _route(row)


async def delete_route(db: AsyncSession, route_key: str, stage_group: str) -> bool:
    """Remove the route (the model is no longer used for it); whether there was one."""
    done = await db.execute(
        text(
            "DELETE FROM model_routes WHERE route_key = :route_key AND stage_group = :stage_group "
            "RETURNING route_key"
        ),
        {"route_key": route_key, "stage_group": stage_group},
    )
    return done.first() is not None


async def route(db: AsyncSession, route_key: str, stage_group: str) -> RouteRow | None:
    row = (
        await db.execute(
            text(
                f"SELECT {_ROUTE_COLUMNS} FROM model_routes "  # noqa: S608
                "WHERE route_key = :route_key AND stage_group = :stage_group"
            ),
            {"route_key": route_key, "stage_group": stage_group},
        )
    ).first()
    return None if row is None else _route(row)


async def routes(db: AsyncSession) -> list[RouteRow]:
    rows = (
        await db.execute(
            text(f"SELECT {_ROUTE_COLUMNS} FROM model_routes ORDER BY route_key, stage_group")  # noqa: S608
        )
    ).all()
    return [_route(r) for r in rows]
