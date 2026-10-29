"""A test-only multi-request class write for the X2 seams (ADR 0007 sections 1, 8 and 9).

``WriteEnv`` runs a ``test_kind`` job whose one step is ``perform`` with kind
``mowl_save_workout`` (the only names the table's check allows) in a registry of its own, so the
real save kind of SV1 is never touched: the call makes **two** requests to the in-process fake
MOWL (``optools.FakeMowlAttach``, through the worker's allowlist opener and the vendored library),
``single_request`` is False, the request has a class, a context and ``bound_seconds``, and
``on_succeeded`` writes a ``saved_workouts`` row the way a save does. The reconciler records what
it was given and answers what the test sets. Public test values only; never the network.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from mowl import api as mowl_api
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import call_opened, hold, open_connection
from classbeat.jobs import Complete, JobFailure, JobKind, JobRun, KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.operations import (
    INCONCLUSIVE,
    CallResult,
    Operation,
    OperationKind,
    OperationKinds,
    OperationRequest,
    Reconciliation,
    orphans,
    perform,
    request_hash,
)
from classbeat.operations.kinds import ReconcileInput
from jobtools import new_job
from optools import OTHER_PLAYLIST, PLAYLIST, OpsEnv

WRITE: Final = "mowl_save_workout"
KIND: Final = "test_kind"
BOUND: Final = 600.0
WAIT: Final = BOUND + 30  # actionable_at - pending_at
HASH: Final = request_hash(WRITE, content="c1")

_SAVED = text(
    "INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, content_hash, "
    "name, operation_id) VALUES (:owner_id, :class_id, :program_id, 1, :hash, 'Ride', :op) "
    "ON CONFLICT DO NOTHING"
)
_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


class WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    key: str | None = None  # default: "<job_id>:save", as SV1's
    note: str = "first"
    probe: bool = False  # only check the unresolved-write rule in a fenced session


@dataclass
class Recorder:
    outcome: Reconciliation = INCONCLUSIVE
    reconciled: list[Operation] = field(default_factory=lambda: list[Operation]())
    succeeded: list[Operation] = field(default_factory=lambda: list[Operation]())
    probes: list[bool] = field(default_factory=lambda: list[bool]())


def write_kind(rec: Recorder) -> OperationKind:
    async def reconcile(check: ReconcileInput) -> Reconciliation:
        rec.reconciled.append(check.operation)
        return rec.outcome

    async def on_succeeded(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
        rec.succeeded.append(operation)
        assert operation.external_id is not None
        await db.execute(
            _SAVED,
            {
                "owner_id": owner_id,
                "class_id": operation.class_id,
                "program_id": int(operation.external_id),
                "hash": hashlib.sha256(b"c1").digest(),
                "op": operation.id,
            },
        )

    return OperationKind(WRITE, reconcile, on_succeeded)


@dataclass
class WriteEnv:
    ops: OpsEnv
    rec: Recorder = field(default_factory=Recorder)

    def __post_init__(self) -> None:
        self.kinds = OperationKinds()
        self.kinds.register(write_kind(self.rec))

    def sql(self, statement: str, **params: Any) -> Any:
        return self.ops.sql(statement, **params)

    def job(
        self, owner: UUID, connection: UUID, class_id: UUID | None = None, **given: Any
    ) -> UUID:
        body = {"connection_id": str(connection), **given}
        return new_job(self.ops.admin, owner, class_id, input=json.dumps(body))

    def class_of(self, job_id: UUID) -> UUID:
        return self.ops.row(job_id).class_id

    def operations(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o AND kind = :k "
            "ORDER BY created_at",
            o=owner,
            k=WRITE,
        ).all()

    def saved(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM saved_workouts WHERE owner_id = :o ORDER BY created_at", o=owner
        ).all()

    def registry(self) -> KindRegistry:
        keyring, kinds, rec = self.ops.keyring, self.kinds, self.rec

        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("save")
            given = job_run.input
            assert isinstance(given, WriteInput)
            lease = job_run.lease
            async with job_run.fenced() as fs:
                class_id = (await fs.db.execute(_CLASS, lease.params())).scalar_one()
                if given.probe:  # the worker path: the job's fenced session
                    rec.probes.append(
                        await orphans.write_unresolved(fs.db, job_run.ctx, class_id, lease.job_id)
                    )
                    return Complete()
            opened = await open_connection(job_run.factory, keyring, job_run.ctx, "mowl")
            if opened.id != given.connection_id:
                raise JobFailure("not_connected")

            async def guard(db: AsyncSession) -> bool:
                return await hold(db, job_run.ctx, opened)

            async def call() -> CallResult:
                answers = [
                    await call_opened(
                        job_run.factory,
                        job_run.ctx,
                        opened,
                        lambda creds, s=source: mowl_api.attach_spotify(creds, s),
                    )
                    for source in (PLAYLIST, OTHER_PLAYLIST)  # two requests: not one
                ]
                return CallResult(str(answers[-1]["PlaylistID"]), {"requests": 2})

            request = OperationRequest(
                kind=WRITE,
                idempotency_key=given.key or f"{lease.job_id}:save",
                request_hash=HASH,
                timeout_seconds=60,
                guard=guard,
                class_id=class_id,
                context={"note": given.note, "baseline": [11, 12]},
                bound_seconds=BOUND,
            )
            await perform(job_run, request, call, keyring=keyring, kinds=kinds)
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(KIND, WriteInput, ("save",), run))
        return registry

    async def claim(self) -> Execution:
        app = self.ops.app
        row = await queue.claim(self.ops.factory, "x2-w", app.settings.job_lease_seconds, [KIND])
        assert row is not None, "nothing to claim"
        return Execution(row)

    async def execute(self, execution: Execution) -> None:
        await execute(
            execution,
            self.ops.factory,
            self.registry(),
            RunConfig(lease_seconds=self.ops.app.settings.job_lease_seconds),
            asyncio.Event(),
        )

    async def run_next(self) -> Execution:
        execution = await self.claim()
        await self.execute(execution)
        return execution

    def lose_lease(self, job_id: UUID) -> None:
        self.sql(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
            id=job_id,
        )

    def age(self, operation_id: UUID, seconds: float) -> None:
        """Move the attempt's ``pending_at`` (and ``call_ended_at``) ``seconds`` into the past."""
        self.sql(
            "UPDATE external_operations SET pending_at = pending_at - make_interval(secs => :s), "
            "call_ended_at = call_ended_at - make_interval(secs => :s) WHERE id = :id",
            s=seconds,
            id=operation_id,
        )
