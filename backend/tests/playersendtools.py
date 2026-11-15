"""Helpers for the send-to-player and removal job tests (ADR 0034 section 5, #1060): an
in-process fake of the player's two source calls behind the worker's player client
(``egress.player_test_client`` over an httpx ``MockTransport``, never the network), a stored
player connection, a planted soundless class and the worker's claim and run.

``FakeSource`` keeps the player's numbers per class as the player does (a higher number is
applied, the same number with the same envelope is a repeat, the same number with another
envelope is a conflict, a lower one is stale) unless ``script`` says otherwise for the next
publication: ``"refuse"`` (never left: a connection error), ``"drop"`` (applied, then the answer
is lost), ``"lost"`` (not applied, the answer lost), ``"401"``, ``"500"``, ``"stale:<N>"`` (409
stale with ``highest_publication_number`` N) or ``"conflict"``. ``reads`` does the same for the
reconciliation's ``GET``: ``"answer"`` (the default), ``"none"`` (no answer) or ``"404"``.
TEST ONLY: public test values, invented users.
"""

import asyncio
import copy
import json
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat import player_send
from classbeat.connections.player_account import store_player_link
from classbeat.egress import player_test_client
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from ctx import make_context
from jobtools import events, job, new_job
from savetools import DRAFT, NO_MUSIC, draft_hash

PUBLICATIONS = "/api/source/publications"
CLASSES = "/api/source/classes/"


def source_token() -> str:
    return "cbp_src_" + secrets.token_urlsafe(32)


@dataclass
class Published:
    number: int
    removed: bool
    envelope: dict[str, Any]


@dataclass
class FakeSource:
    classes: dict[str, Published] = field(default_factory=lambda: dict[str, Published]())
    script: list[str] = field(default_factory=lambda: list[str]())
    reads: list[str] = field(default_factory=lambda: list[str]())
    posts: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    gets: list[str] = field(default_factory=lambda: list[str]())
    bearers: list[str] = field(default_factory=lambda: list[str]())

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.bearers.append(request.headers.get("authorization", ""))
        if request.method == "POST" and request.url.path == PUBLICATIONS:
            return self._publish(request, json.loads(request.content))
        if request.method == "GET" and request.url.path.startswith(CLASSES):
            return self._read(request, request.url.path.removeprefix(CLASSES))
        return httpx.Response(404, json={"error": "not_found"})

    def _publish(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        self.posts.append(body)
        step = self.script.pop(0) if self.script else "real"
        if step == "refuse":
            raise httpx.ConnectError("the fake player refused", request=request)
        if step == "401":
            return httpx.Response(401, json={"error": "unauthorized"})
        if step == "500":
            return httpx.Response(500, json={"error": "internal"})
        if step == "conflict":
            return self._error("publication_conflict", body)
        if step.startswith("stale:"):
            return httpx.Response(
                409,
                json={
                    "error": "stale_publication",
                    "highest_publication_number": int(step.removeprefix("stale:")),
                },
            )
        if step == "lost":
            raise httpx.ReadTimeout("the answer was lost", request=request)
        answer = self._apply(body)
        if step == "drop":
            raise httpx.ReadTimeout("the answer was lost", request=request)
        return answer

    def _apply(self, body: dict[str, Any]) -> httpx.Response:
        class_id, number = body["source_class_id"], body["publication_number"]
        held = self.classes.get(class_id)
        if held is None or number > held.number:
            self.classes[class_id] = Published(number, body["removed"], copy.deepcopy(body))
            return httpx.Response(
                200, json={"status": "applied", "highest_publication_number": number}
            )
        if number == held.number and held.envelope == body:
            return httpx.Response(
                200, json={"status": "repeat", "highest_publication_number": number}
            )
        if number == held.number:
            return self._error("publication_conflict", body)
        return self._error("stale_publication", body)

    def _error(self, error: str, body: dict[str, Any]) -> httpx.Response:
        held = self.classes.get(body["source_class_id"])
        highest = 0 if held is None else held.number
        return httpx.Response(409, json={"error": error, "highest_publication_number": highest})

    def _read(self, request: httpx.Request, class_id: str) -> httpx.Response:
        self.gets.append(class_id)
        step = self.reads.pop(0) if self.reads else "answer"
        held = self.classes.get(class_id)
        if step == "none":
            raise httpx.ReadTimeout("no answer", request=request)
        if step == "404" or held is None:
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(
            200,
            json={
                "source_class_id": class_id,
                "highest_publication_number": held.number,
                "removed": held.removed,
            },
        )


@dataclass
class Planted:
    owner: UUID
    class_id: UUID
    revision_id: UUID
    content: dict[str, Any]

    @property
    def hash_hex(self) -> str:
        return draft_hash(self.content).hex()

    def input(self, **overrides: Any) -> dict[str, Any]:
        return {
            "class_id": str(self.class_id),
            "revision_id": str(self.revision_id),
            "content_hash": self.hash_hex,
            **overrides,
        }


@dataclass
class SendEnv:
    app: Env
    admin: Connection
    fake: FakeSource

    @property
    def factory(self) -> Any:
        return self.app.factory

    @property
    def keyring(self) -> Any:
        return self.app.app.state.keyring

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    async def user(self) -> UUID:
        return await self.app.user(f"send-{uuid.uuid4().hex[:10]}@example.com")

    async def connect(self, owner: UUID) -> str:
        issued = source_token()
        await store_player_link(self.factory, self.keyring, make_context(owner), issued, None)
        return issued

    def set_status(self, owner: UUID, status: str) -> None:
        self.sql(
            "UPDATE connections SET status = :s WHERE owner_id = :o AND provider = 'player'",
            s=status, o=owner,
        )  # fmt: skip

    def status(self, owner: UUID) -> str | None:
        return self.sql(
            "SELECT status FROM connections WHERE owner_id = :o AND provider = 'player'", o=owner
        ).scalar_one_or_none()

    def plant(self, owner: UUID, content: dict[str, Any] | None = None) -> Planted:
        """A soundless class with one draft revision (its content ``DRAFT`` unless given)."""
        content = copy.deepcopy(DRAFT if content is None else content)
        class_id = self.sql(
            "INSERT INTO classes (id, owner_id, title, music_source) VALUES (:id, :o, "
            "'Tuesday climb', CAST(:music AS jsonb)) RETURNING id",
            id=uuid.uuid4(), o=owner, music=json.dumps(NO_MUSIC),
        ).scalar_one()  # fmt: skip
        revision_id = self.revision(owner, class_id, content)
        return Planted(owner, class_id, revision_id, content)

    def revision(self, owner: UUID, class_id: UUID, content: dict[str, Any]) -> UUID:
        return self.sql(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) SELECT :id, :o, :c, coalesce(max(number), 0) + 1, "
            "'draft', CAST(:content AS jsonb), :h, CAST(:music AS jsonb), '{}'::jsonb "
            "FROM class_revisions WHERE owner_id = :o AND class_id = :c RETURNING id",
            id=uuid.uuid4(), o=owner, c=class_id, content=json.dumps(content),
            h=draft_hash(content), music=json.dumps(NO_MUSIC),
        ).scalar_one()  # fmt: skip

    def job(self, planted: Planted, **overrides: Any) -> UUID:
        """A queued send as the jobs API stores it (no ``admit``: the worker's own checks)."""
        return new_job(
            self.admin,
            planted.owner,
            planted.class_id,
            kind=player_send.KIND,
            input=json.dumps(planted.input(**overrides)),
            credit_state="none",
        )

    def registry(self) -> KindRegistry:
        registry = KindRegistry()
        registry.register(player_send.SEND_TO_PLAYER)
        registry.register(player_send.PLAYER_REMOVE)
        return registry

    async def run_next(self) -> UUID:
        lease = self.app.settings.job_lease_seconds
        kinds = [player_send.KIND, player_send.PLAYER_REMOVE.name]
        row = await queue.claim(self.factory, "pc5-w", lease, kinds)
        assert row is not None, "nothing to claim"
        execution = Execution(row)
        await execute(
            execution, self.factory, self.registry(), RunConfig(lease_seconds=lease),
            asyncio.Event(),
        )  # fmt: skip
        return row.job_id

    def row(self, job_id: UUID) -> Any:
        return job(self.admin, job_id)

    def ready(self, job_id: UUID) -> None:
        self.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)

    def operations(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o "
            "AND kind IN ('player_send', 'player_remove') ORDER BY created_at",
            o=owner,
        ).all()

    def publication(self, owner: UUID, class_id: UUID) -> Any:
        return self.sql(
            "SELECT * FROM player_publications WHERE owner_id = :o AND source_class_id = :c",
            o=owner, c=class_id,
        ).first()  # fmt: skip

    def events(self, job_id: UUID) -> list[tuple[int, str, dict[str, Any], int]]:
        return events(self.admin, job_id)


@pytest.fixture
def sendenv(env: Env, jobs_admin: Connection) -> Iterator[SendEnv]:
    fake = FakeSource()

    def client() -> httpx.AsyncClient:
        return player_test_client(httpx.MockTransport(fake.handle))

    player_send.configure(player_send.Runtime(env.app.state.keyring, client_factory=client))
    try:
        yield SendEnv(env, jobs_admin, fake)
    finally:
        player_send.configure(None)
