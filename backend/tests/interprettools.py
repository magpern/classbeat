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
from classbeat import connections, design
from classbeat.agent.interpret import FINAL_TOOL
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.design import DESIGN_DRAFT, INTERPRET_REQUEST, Runtime
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from ctx import make_context
from designtools import PLAYLIST
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
    spotify_id: UUID | None = None

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

    async def connect_spotify(self) -> UUID:
        """A Spotify connection of the user (fake tokens; nothing reaches Spotify), once."""
        if self.spotify_id is None:
            ops = self.renv.denv.ops
            tokens = {"access_token": "a", "refresh_token": "r", "expires_at": 4_000_000_000}
            self.spotify_id = await connections.save(
                ops.factory,
                ops.keyring,
                make_context(self.owner),
                "spotify",
                tokens,
                public={"client_id": "c" * 32, "spotify_user_id": "rider"},
                confirm_account_change=True,
            )
        return self.spotify_id

    async def start(
        self, request: str = REQUEST, *, spotify: bool = True, **more: Any
    ) -> httpx.Response:
        """Starts an interpretation. By default the user has Spotify and chose the MOWL playlist
        (roadmap Amendment 25: a cycling class has no default music); ``spotify=False`` sends the
        bare request, as a user with no Spotify connection would."""
        values: dict[str, Any] = {
            "request": request,
            "mowl_connection_id": str(self.renv.browser.connection_id),
        }
        if spotify:
            values["spotify_connection_id"] = str(await self.connect_spotify())
            values["music_override"] = {
                "source": "mowl_spotify_playlist",
                "spotify_playlist_id": PLAYLIST,
            }
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

    async def interpreted(
        self, reading: dict[str, Any] | None = None, **more: Any
    ) -> tuple[UUID, UUID]:
        """A class whose interpretation is stored: (class id, interpret job id); ``more`` is
        ``start``'s."""
        self.models(interpreter(reading))
        made = await self.start(**more)
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
    # The user starts with no Spotify connection; ``connect_spotify`` makes one when asked.
    renv.sql("DELETE FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=renv.owner)
    made = InterpretEnv(renv)
    try:
        yield made
    finally:
        design.configure(None)
