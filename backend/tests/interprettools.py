"""Helpers for the ``interpret_request`` tests (ADR 0029 section 5, MA1b-3): a class started from
one request through the jobs API, interpreted by the worker side with the fake MOWL and a
``FakeProvider`` that submits a fixed reading, then handed off to a design. Never the network, a
real account or a model."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest

from agentfake import CONFIG, FactoryModels
from classbeat import design
from classbeat.agent.interpret import FINAL_TOOL
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.design import DESIGN_DRAFT, INTERPRET_REQUEST, Runtime
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from jobapi import new_key
from revisetools import ReviseEnv

REQUEST = "A 30 minute class with one climb and two sprints, please"
# What a good interpreter reads in ``REQUEST``: inferred values, so the design stage has nothing
# stated to keep and the recorded ``good_first_draft`` script fits it.
READING: dict[str, Any] = {
    "length_min": {"value": 30, "origin": "inferred", "quote": "30 minute"},
    "climbs": {"value": 1, "origin": "inferred"},
    "sprints": {"value": 2, "origin": "inferred"},
}


def interpreter(reading: dict[str, Any] | None = None) -> FakeProvider:
    call = {"name": FINAL_TOOL, "input": reading or READING, "expect": {"accepted": True}}
    return FakeProvider(
        FakeScript.model_validate({"model": "fake-model", "turns": [{"calls": [call]}]})
    )


def refusing() -> FakeProvider:
    return FakeProvider(FakeScript.model_validate({"turns": [{"refusal": True}]}))


@dataclass
class InterpretEnv:
    renv: ReviseEnv
    queue_: list[ModelProvider] = field(default_factory=lambda: list[ModelProvider]())

    @property
    def sql(self) -> Any:
        return self.renv.sql

    @property
    def owner(self) -> UUID:
        return self.renv.owner

    def models(self, *providers: ModelProvider) -> None:
        """The providers the next runs use, one per stage group and attempt, in order."""
        self.queue_[:] = providers

        def build() -> ModelProvider:
            return self.queue_.pop(0)

        design.configure(
            Runtime(keyring=self.renv.denv.ops.keyring, models=FactoryModels(build), agent=CONFIG)
        )

    async def start(self, request: str = REQUEST, **more: Any) -> httpx.Response:
        values = {"request": request, "mowl_connection_id": str(self.renv.browser.connection_id)}
        body = {"kind": INTERPRET_REQUEST.name, "input": values | more}
        return await self.renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})

    async def run(self, kind: str) -> Execution:
        settings = self.renv.denv.app.settings
        registry = KindRegistry()
        registry.register(INTERPRET_REQUEST)
        registry.register(DESIGN_DRAFT)
        row = await queue.claim(
            self.renv.denv.ops.factory, "ma-w", settings.job_lease_seconds, [kind]
        )
        assert row is not None, "nothing to claim"
        execution = Execution(row)
        await execute(
            execution,
            self.renv.denv.ops.factory,
            registry,
            RunConfig(lease_seconds=settings.job_lease_seconds),
            asyncio.Event(),
        )
        return execution

    async def interpreted(self, reading: dict[str, Any] | None = None) -> tuple[UUID, UUID]:
        """A class whose interpretation is stored: (class id, interpret job id)."""
        self.models(interpreter(reading))
        made = await self.start()
        assert made.status_code == 201, made.text
        job_id = UUID(made.json()["id"])
        await self.run(INTERPRET_REQUEST.name)
        row = self.renv.denv.ops.row(job_id)
        assert (row.state, row.error_category) == ("completed", None)
        return UUID(made.json()["class_id"]), job_id

    async def handoff(self, class_id: UUID, **body: Any) -> httpx.Response:
        return await self.renv.browser.post(
            f"/api/classes/{class_id}/design-from-interpretation", body or None
        )

    def interpretation(self, class_id: UUID) -> Any:
        return self.sql("SELECT interpretation FROM classes WHERE id = :c", c=class_id).scalar()

    def counts(self) -> tuple[int, int, int]:
        """(classes, jobs, credit ledger rows) of the user."""
        o = self.owner
        return (
            self.sql("SELECT count(*) FROM classes WHERE owner_id = :o", o=o).scalar_one(),
            self.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=o).scalar_one(),
            self.sql("SELECT count(*) FROM credit_ledger WHERE owner_id = :o", o=o).scalar_one(),
        )

    def credits(self) -> int:
        return self.counts()[2]

    def design_jobs(self, class_id: UUID) -> list[Any]:
        return self.sql(
            "SELECT id, kind, state, credit_state, idempotency_key FROM jobs "
            "WHERE class_id = :c AND kind <> 'interpret_request' ORDER BY created_at",
            c=class_id,
        ).all()

    def stored_input(self, job_id: UUID) -> dict[str, Any]:
        raw = self.sql("SELECT input FROM jobs WHERE id = :j", j=job_id).scalar_one()
        return raw if isinstance(raw, dict) else json.loads(raw)


@pytest.fixture
async def ienv(renv: ReviseEnv) -> AsyncIterator[InterpretEnv]:
    made = InterpretEnv(renv)
    try:
        yield made
    finally:
        design.configure(None)
