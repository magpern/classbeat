"""Helpers for the ``update_workout`` tests (SV2): a class already saved to the fake MOWL by a real
``save_workout`` job (a live program, a verified saved row), a second revision to update it to,
update jobs and a signed-in browser for Update. The in-process fake of ``savetools``; never the
network.
"""

import copy
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest

from authtools import ORIGIN, Env
from classbeat import saving
from classbeat.saving import update_operation
from jobapi import Browser, new_key
from jobtools import new_job
from optools import TOKEN_A
from savetools import DRAFT, FakeMowlPrograms, Saved, SaveEnv, draft_hash

# Revision 2: the same name and description as DRAFT, another first interval. An update to it
# that stops part-way leaves the program's header as it was, so the target still equals its
# ``before_hash`` (the failure-injection tests rely on that).
DRAFT_2: dict[str, Any] = copy.deepcopy(DRAFT)
DRAFT_2["segments"][1]["intervals"][0]["zone"] = 5
# Revision 3: renamed. An update to it that stops after the header is a partial state.
DRAFT_RENAMED: dict[str, Any] = copy.deepcopy(DRAFT_2)
DRAFT_RENAMED["name"] = "Tuesday climb, harder"


@dataclass
class Live:
    """A class with a saved program: the plant, the saved row and the program."""

    saved: Saved
    saved_workout_id: UUID
    program_id: int

    def input(
        self,
        revision_id: UUID | None = None,
        content: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        base = self.saved.input()
        if revision_id is not None:
            base["revision_id"] = str(revision_id)
        if content is not None:
            base["content_hash"] = draft_hash(content).hex()
        return {
            **base,
            "saved_workout_id": str(self.saved_workout_id),
            "overwrite": False,
            **overrides,
        }


@dataclass
class UpdateEnv:
    s: SaveEnv

    @property
    def fake(self) -> FakeMowlPrograms:
        return self.s.fake

    def sql(self, statement: str, **params: Any) -> Any:
        return self.s.sql(statement, **params)

    async def live(self, token: str = TOKEN_A) -> Live:
        """A new user's class saved by a real Save job: one live, verified saved row."""
        return await self._saved(await self.s.saved_class(token))

    async def sibling(self, live: Live) -> Live:
        """Another class of the same user, saved the same way."""
        return await self._saved(self.s.plant(live.saved.owner, live.saved.connection))

    async def _saved(self, saved: Saved) -> Live:
        job_id = self.s.job(saved)
        await self.s.run_next()
        assert self.s.row(job_id).state == "completed"
        [row] = [r for r in self.s.saved_rows(saved.owner) if r.class_id == saved.class_id]
        assert row.state_hash is not None
        return Live(saved, row.id, row.program_id)

    def revision(self, live: Live, content: dict[str, Any] = DRAFT_2) -> UUID:
        return self.s.plant_revision(
            live.saved.owner, live.saved.class_id, live.saved.link_id, content
        )

    def job(
        self,
        live: Live,
        revision_id: UUID | None = None,
        content: dict[str, Any] | None = None,
        columns: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> UUID:
        """A queued update job as the jobs API stores it (no ``admit``: the worker's checks)."""
        return new_job(
            self.s.ops.admin,
            live.saved.owner,
            live.saved.class_id,
            kind=saving.UPDATE_KIND,
            input=json.dumps(live.input(revision_id, content, **overrides)),
            credit_state="none",
            **(columns or {}),
        )

    async def run_next(self) -> Any:
        return await self.s.run_next()

    def row(self, job_id: UUID) -> Any:
        return self.s.row(job_id)

    def operations(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o AND kind = :k "
            "ORDER BY created_at",
            o=owner, k=update_operation.KIND,
        ).all()  # fmt: skip

    def saved_row(self, saved_workout_id: UUID) -> Any:
        return self.sql("SELECT * FROM saved_workouts WHERE id = :id", id=saved_workout_id).one()

    def op_states(self, job_id: UUID) -> list[str]:
        return self.s.op_states(job_id)

    def stages(self, job_id: UUID) -> list[str]:
        return [p["stage"] for _, k, p, _ in self.s.ops.events(job_id) if k == "stage_started"]

    def ready(self, job_id: UUID) -> None:
        self.s.ready(job_id)

    def counts(self) -> tuple[int, int]:
        """(write requests, program reads) the fake has seen."""
        return self.fake.write_count, self.fake.gets


@pytest.fixture
def uenv(senv: SaveEnv) -> UpdateEnv:
    return UpdateEnv(senv)


@dataclass
class UpdateBrowser:
    """A signed-in browser of a live class's owner, through the real jobs API."""

    browser: Browser
    live: Live

    async def update(
        self,
        revision_id: UUID | None = None,
        content: dict[str, Any] | None = None,
        key: str | None = None,
        **overrides: Any,
    ) -> httpx.Response:
        body = {
            "kind": saving.UPDATE_KIND,
            "input": self.live.input(revision_id, content, **overrides),
        }
        return await self.browser.post("/api/jobs", body, {"Idempotency-Key": key or new_key()})

    async def save(self, **overrides: Any) -> httpx.Response:
        body = {"kind": saving.KIND, "input": self.live.saved.input(**overrides)}
        return await self.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


@pytest.fixture
async def update_browser(env: Env, uenv: UpdateEnv) -> AsyncIterator[Any]:
    clients: list[httpx.AsyncClient] = []

    async def make(live: Live) -> UpdateBrowser:
        issued = await env.login(live.saved.owner)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN)
        clients.append(client)
        browser = Browser(env, issued.token, client, live.saved.owner, live.saved.connection)
        return UpdateBrowser(browser, live)

    yield make
    for client in clients:
        await client.aclose()
