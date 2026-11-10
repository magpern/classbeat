"""``agent_runs`` (ADR 0006 section 6), split out of ``classbeat.agent.records``.

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
"""

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import text

from classbeat.agent.loop import Counters, RunInfo
from classbeat.agent.prices import PRICES_VERSION
from classbeat.jobs.run import JobRun

_SPENT = text(
    "SELECT coalesce(sum(cost_micros), 0) FROM agent_runs "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)
_INSERT = text(
    "INSERT INTO agent_runs (id, owner_id, job_id, job_kind, price_version, stage, attempt, "
    "provider, model, effort, prompt_version, route_key, eval_result_id) VALUES (:id, :owner_id, "
    ":job_id, :job_kind, :price_version, :stage, :attempt, :provider, :model, :effort, "
    ":prompt_version, :route_key, :eval_result_id)"
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
    """``job_stage``: the job's stage this run belongs to, when it is not named like the agent
    stage (``design_with_songs``' ``pool_search`` runs ``pick_pool``, ADR 0008 section 4); the
    ``agent_runs`` row keeps the agent stage's name either way."""

    def __init__(self, run: JobRun, job_stage: str | None = None) -> None:
        self.run = run
        self.job_stage = job_stage

    def _ids(self) -> dict[str, Any]:
        return {"owner_id": self.run.lease.owner_id, "job_id": self.run.lease.job_id}

    async def checkpoint(self) -> None:
        await self.run.checkpoint()

    async def begin(self, info: RunInfo) -> tuple[UUID, int]:
        # A checkpoint, the job's stage and stage_started.
        await self.run.stage(self.job_stage or info.stage)
        run_id = uuid.uuid4()
        async with self.run.fenced() as fs:
            spent = (await fs.db.execute(_SPENT, self._ids())).scalar_one()
            await fs.db.execute(
                _INSERT,
                {
                    **self._ids(),
                    "id": run_id,
                    "job_kind": self.run.kind,
                    "price_version": PRICES_VERSION,
                    "stage": info.stage,
                    "attempt": self.run.attempt,
                    "provider": info.provider,
                    "model": info.model,
                    "effort": info.effort,
                    "prompt_version": info.prompt_version,
                    "route_key": info.route_key,
                    "eval_result_id": info.eval_result_id,
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
