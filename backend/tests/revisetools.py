"""Helpers for the ``revise_draft`` tests (R1): a class designed through the jobs API and the
worker side with the fake MOWL and ``FakeProvider``, then revised the same way. A provider is
built per run from ``scripts`` (the first name, then the next ...), so a retry gets a new one."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest

from agentfake import CONFIG, SCRIPTS, FactoryModels, from_dict, provider
from classbeat import design
from classbeat.agent.providers.fake import FakeProvider
from classbeat.design import DESIGN_DRAFT, REVISE_DRAFT, Runtime
from classbeat.design import limits as design_limits
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from designtools import BRIEF, DesignEnv
from jobapi import Browser, new_key
from optools import PLAYLIST, TOKEN_A


@dataclass
class ReviseEnv:
    denv: DesignEnv
    browser: Browser
    scripts: list[str] = field(default_factory=lambda: list[str]())
    providers: list[FakeProvider] = field(default_factory=lambda: list[FakeProvider]())
    context_requests: int = 10
    running_covered: bool = True  # False: the route's result does not cover running (#465)
    soundless_covered: bool = True  # False: the route's result does not cover soundless (#527)

    def runtime(self) -> Runtime:
        def build() -> FakeProvider:
            name = self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]
            made = provider(name)
            self.providers.append(made)
            return made

        return Runtime(
            keyring=self.denv.ops.keyring,
            models=FactoryModels(
                build,
                running_covered=self.running_covered,
                soundless_covered=self.soundless_covered,
            ),
            agent=CONFIG,
            context_requests=self.context_requests,
        )

    def use(self, *scripts: str) -> None:
        self.scripts[:] = scripts
        design.configure(self.runtime())

    def use_interpreted(self, name: str, reading: dict[str, Any]) -> None:
        """Every run gets script ``name`` with a ``submit_brief`` of ``reading`` first: a revise of
        a class with stated values reads its request with the ``interpret`` stage (#904)."""
        data = json.loads((SCRIPTS / f"{name}.json").read_text("utf-8"))
        call = {"name": "submit_brief", "input": reading, "expect": {"accepted": True}}
        data["turns"] = [{"calls": [call]}, *data["turns"]]

        def build() -> FakeProvider:
            made = from_dict(data)
            self.providers.append(made)
            return made

        models = FactoryModels(
            build, running_covered=self.running_covered, soundless_covered=self.soundless_covered
        )
        design.configure(
            Runtime(
                keyring=self.denv.ops.keyring,
                models=models,
                agent=CONFIG,
                context_requests=self.context_requests,
            )
        )

    def state(self, class_id: UUID, explicit: list[str]) -> None:
        """Plant ``explicit`` on the class's stored brief: values its instructor stated."""
        brief = self.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
        self.sql(
            "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c",
            b=json.dumps({**brief, "explicit": explicit}),
            c=class_id,
        )

    @property
    def owner(self) -> UUID:
        return self.browser.user_id

    @property
    def sql(self) -> Any:
        return self.denv.ops.sql

    async def run(self, kind: str) -> Execution:
        settings = self.denv.app.settings
        registry = KindRegistry()
        registry.register(DESIGN_DRAFT)
        registry.register(REVISE_DRAFT)
        row = await queue.claim(self.denv.ops.factory, "r1-w", settings.job_lease_seconds, [kind])
        assert row is not None, "nothing to claim"
        execution = Execution(row)
        await execute(
            execution,
            self.denv.ops.factory,
            registry,
            RunConfig(lease_seconds=settings.job_lease_seconds),
            asyncio.Event(),
        )
        return execution

    async def design(self, script: str = "good_first_draft") -> tuple[UUID, UUID]:
        """A designed class: (class id, its first revision's id)."""
        self.use(script)
        body = {
            "kind": design.KIND,
            "input": {
                "title": "Tuesday climb",
                "brief": BRIEF,
                "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
                "mowl_connection_id": str(self.browser.connection_id),
            },
        }
        made = await self.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
        assert made.status_code == 201, made.text
        job_id = UUID(made.json()["id"])
        await self.run(design.KIND)
        assert self.denv.ops.row(job_id).state == "completed"
        return UUID(made.json()["class_id"]), self.denv.revisions(job_id)[0].id

    async def ask(
        self, class_id: UUID, base: UUID, request: str = "Make the start easier.", **more: Any
    ) -> httpx.Response:
        values = {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "request": request,
            "mowl_connection_id": str(self.browser.connection_id),
        }
        body = {"kind": design.REVISE_KIND, "input": values | more}
        return await self.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})

    def current(self, class_id: UUID) -> UUID | None:
        return self.sql(
            "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
        ).scalar()

    def messages(self, class_id: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM class_messages WHERE class_id = :c ORDER BY seq", c=class_id
        ).all()

    def revisions(self, class_id: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM class_revisions WHERE class_id = :c ORDER BY number", c=class_id
        ).all()

    async def revise(
        self, class_id: UUID, base: UUID, script: str, request: str = "Make the start easier."
    ) -> tuple[UUID, Execution]:
        self.use(script)
        made = await self.ask(class_id, base, request)
        assert made.status_code == 201, made.text
        job_id = UUID(made.json()["id"])
        return job_id, await self.run(design.REVISE_KIND)


@pytest.fixture
async def renv(browsers: Any, denv: DesignEnv, fake_mowl: Any) -> AsyncIterator[ReviseEnv]:
    browser: Browser = await browsers()
    denv.app.app.dependency_overrides.clear()  # the real registry, as in production
    await denv.ops.connect(browser.user_id, TOKEN_A)  # in place: the same connection id
    made = ReviseEnv(denv, browser)
    try:
        yield made
    finally:
        design_limits.configure(None)
