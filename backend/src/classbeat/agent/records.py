"""``agent_runs`` and revision provenance (ADR 0006 section 6).

``JobStageHost(job_run)`` is the worker's ``StageHost``: its checkpoint is the job's
(``JobRun.checkpoint``), and every write goes through ``JobRun.fenced()``, the fenced transaction
of ADR 0005 section 3, so a worker that lost its lease can't add or update an ``agent_runs`` row
or an event (``LeaseLost``, the run stops). One row per stage run: inserted when the run starts
(``outcome`` NULL), its counters updated after every turn, its outcome and ``finished_at`` set
when it ends. A turn that finishes after the lease was lost can't be recorded, so the job's cost
across attempts may undercount by that one turn per lost lease (accepted in ADR 0006 section 6).

A shutdown hand-back or a lost lease leaves ``outcome`` and ``finished_at`` NULL for good (and
``lease_lost`` is never written; the fence and ADR 0005 say so). A query for runs in progress
must therefore look at the job's state, not at ``outcome IS NULL``.

``Provenance`` is what a revision records about how its content was made; ``provenance(...)``
builds it from the stage runs and the music map. Besides ``map_version`` it records the map's
``crossfade``: ``music.MAP_VERSION`` stayed 12 across ``mowl-workouts`` 0.5.0, so ``map_version``
alone no longer tells a back-to-back map from an overlapped one.
"""

import uuid
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import UUID

from mowl import music, spotify
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from classbeat import __version__ as classbeat_version
from classbeat.agent.loop import Counters, RunInfo, StageOutcome
from classbeat.jobs.run import JobRun

_SPENT = text(
    "SELECT coalesce(sum(cost_micros), 0) FROM agent_runs "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)
_INSERT = text(
    "INSERT INTO agent_runs (id, owner_id, job_id, stage, attempt, provider, model, effort, "
    "prompt_version) VALUES (:id, :owner_id, :job_id, :stage, :attempt, :provider, :model, "
    ":effort, :prompt_version)"
)
_SET_COUNTERS = (
    "UPDATE agent_runs SET turns = :turns, tool_calls = :tool_calls, "
    "input_tokens = :input_tokens, output_tokens = :output_tokens, "
    "cache_read_tokens = :cache_read_tokens, cache_write_tokens = :cache_write_tokens, "
    "cost_micros = :cost_micros, model_answered = :model_answered"
)
# Only an open row of this owner's job: a finished run is never written again.
_OPEN_ROW = " WHERE id = :id AND owner_id = :owner_id AND job_id = :job_id AND outcome IS NULL"
_RECORD = text(_SET_COUNTERS + _OPEN_ROW)
_FINISH = text(_SET_COUNTERS + ", outcome = :outcome, finished_at = now()" + _OPEN_ROW)


def _counter_params(counters: Counters) -> dict[str, Any]:
    u = counters.usage
    return {
        "turns": counters.turns,
        "tool_calls": counters.tool_calls,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": u.cache_read_tokens,
        "cache_write_tokens": u.cache_write_tokens,
        "cost_micros": counters.cost_micros,
        "model_answered": counters.model_answered,
    }


class JobStageHost:
    def __init__(self, run: JobRun) -> None:
        self.run = run

    def _ids(self) -> dict[str, Any]:
        return {"owner_id": self.run.lease.owner_id, "job_id": self.run.lease.job_id}

    async def checkpoint(self) -> None:
        await self.run.checkpoint()

    async def begin(self, info: RunInfo) -> tuple[UUID, int]:
        await self.run.stage(info.stage)  # a checkpoint, the job's stage and stage_started
        run_id = uuid.uuid4()
        async with self.run.fenced() as fs:
            spent = (await fs.db.execute(_SPENT, self._ids())).scalar_one()
            await fs.db.execute(
                _INSERT,
                {
                    **self._ids(),
                    "id": run_id,
                    "stage": info.stage,
                    "attempt": self.run.attempt,
                    "provider": info.provider,
                    "model": info.model,
                    "effort": info.effort,
                    "prompt_version": info.prompt_version,
                },
            )
        return run_id, int(spent)

    async def record(self, run_id: UUID, counters: Counters) -> None:
        async with self.run.fenced() as fs:
            await fs.db.execute(_RECORD, {**self._ids(), "id": run_id, **_counter_params(counters)})

    async def tool_called(self, tool: str, error: str | None) -> None:
        payload: dict[str, Any] = {"tool": tool, "ok": error is None}
        if error is not None:
            payload["error"] = error
        async with self.run.fenced() as fs:
            await fs.event("tool_called", payload)

    async def finish(self, run_id: UUID, counters: Counters, outcome: str) -> None:
        async with self.run.fenced() as fs:
            await fs.db.execute(
                _FINISH,
                {**self._ids(), "id": run_id, **_counter_params(counters), "outcome": outcome},
            )


def mowl_version() -> str:
    try:
        return version("mowl-workouts")
    except PackageNotFoundError:  # pragma: no cover - the wheel is always installed
        return "0+unknown"


class ProvenanceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)


class Provenance(BaseModel):
    """``class_revisions.provenance`` (ADR 0006 section 6)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=100)
    model_answered: str = Field(max_length=100)
    effort: str = Field(min_length=1, max_length=20)
    prompt_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    classbeat_version: str = Field(min_length=1, max_length=64)
    mowl_version: str = Field(min_length=1, max_length=64)
    map_version: int = Field(ge=1)
    crossfade_sec: float = Field(ge=0, le=12)
    agent_run_ids: list[UUID] = Field(min_length=1)
    usage: ProvenanceUsage
    cost_micros: int = Field(ge=0)


def provenance(runs: Sequence[StageOutcome], music_map: Mapping[str, Any]) -> Provenance:
    """From the job's stage runs (the last one made the content) and the map it was fitted to."""
    if not runs:
        raise ValueError("a revision is made by at least one stage run")
    last = runs[-1]
    usage = runs[0].usage
    for run in runs[1:]:
        usage = usage + run.usage
    return Provenance(
        provider=last.info.provider,
        model=last.info.model,
        model_answered=last.model_answered,
        effort=last.info.effort,
        prompt_version=last.info.prompt_version,
        classbeat_version=classbeat_version,
        mowl_version=mowl_version(),
        map_version=int(music_map.get("version", music.MAP_VERSION)),
        # As fitted: a stored map without the key is converted to the default first.
        crossfade_sec=float(music_map.get("crossfade", spotify.CROSSFADE_SEC)),
        agent_run_ids=[r.run_id for r in runs],
        usage=ProvenanceUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        ),
        cost_micros=sum(r.cost_micros for r in runs),
    )
