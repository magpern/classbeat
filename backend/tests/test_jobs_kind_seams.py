"""The job-kind seams of ADR 0007 section 3 (K1, #142), through the real jobs API on the app role
under RLS with test-only kinds: ``consumes_credit`` (no ledger call, ``credit_state = 'none'``,
ignored by settle) and the create-time ``admit`` (under the owner lock, after the idempotency
lookup, in a savepoint; its server fields). No provider is contacted."""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry, Outcome, queue, settle
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.jobs.routes import canonical_json, credit_ledger, job_kinds
from classbeat.jobs.runner import Execution, RunConfig, execute
from jobapi import LIST_HASH, Browser, new_key, owner_lock_key, two_phases
from jobcore import KIND, JobsEnv, store_draft
from jobtools import engine_for

pytestmark = pytest.mark.usefixtures("jenv")

FREE = "free_kind"  # consumes no credit, waits once for approval like the test kind
ADMITTED = "admitted_kind"  # consumes no credit, has admit and one server field
SERVER_VALUE = "resolved-by-the-server"


class FreeInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)
    mowl_connection_id: UUID


class AdmittedInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)
    mowl_connection_id: UUID
    want: Literal["ok", "refuse", "write_then_refuse"] = "ok"
    resolved: str | None = None  # a server field: only admit sets it


class LedgerSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID]] = []

    async def reserve(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        self.calls.append(("reserve", job_id))

    async def allows(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> bool:
        self.calls.append(("allows", job_id))
        return True

    async def commit(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        self.calls.append(("commit", job_id))

    async def release(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> None:
        self.calls.append(("release", job_id))


@dataclass
class Admits:
    """The test kind's ``admit``: what it saw, whether the owner lock was held (tried from
    another connection, which must fail), and the job ids ``after_insert`` was given."""

    admin: Engine | None = None
    calls: int = 0
    locked: list[bool] = field(default_factory=list[bool])
    inserted: list[UUID] = field(default_factory=list[UUID])
    planted: UUID = field(default_factory=uuid.uuid4)

    def owner_lock_held(self, owner: UUID) -> bool:
        assert self.admin is not None
        with self.admin.connect() as other:
            got = other.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": owner_lock_key(owner)},
            ).scalar_one()
            other.rollback()  # releases it if it was free
        return not got

    async def admit(self, db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Any:
        assert isinstance(parsed, AdmittedInput)
        self.calls += 1
        self.locked.append(self.owner_lock_held(ctx.user_id))
        if parsed.want == "refuse":
            return Refused("stale_base", {"limit": 3, "count": 3})
        if parsed.want == "write_then_refuse":
            await db.execute(
                text("INSERT INTO classes (id, owner_id, title) VALUES (:i, :o, 'planted')"),
                {"i": self.planted, "o": ctx.user_id},
            )
            return Refused("revision_limit")

        async def after_insert(db: AsyncSession, job_id: UUID) -> None:
            found = await db.execute(
                text("SELECT input ->> 'resolved' FROM jobs WHERE id = :i AND owner_id = :o"),
                {"i": job_id, "o": ctx.user_id},
            )
            assert found.scalar_one() == SERVER_VALUE  # the row exists, with the admitted input
            self.inserted.append(job_id)

        return Admitted({"resolved": SERVER_VALUE}, after_insert)


async def completes(job_run: JobRun) -> Outcome:
    return Complete(store_draft)


@pytest.fixture
def seams(env: Any, browsers: Any, database_url: str) -> Iterator[tuple[Admits, LedgerSpy]]:
    """The test kind (credit), ``free_kind`` and ``admitted_kind`` behind the jobs API, with a
    spying ledger. ``browsers`` is requested first, so these overrides replace its registry."""
    admits = Admits()
    spy = LedgerSpy()
    registry = KindRegistry()
    registry.register(JobKind(KIND, FreeInput, ("pick", "design"), two_phases))
    registry.register(
        JobKind(FREE, FreeInput, ("pick", "design"), two_phases, consumes_credit=False)
    )
    registry.register(
        JobKind(
            ADMITTED,
            AdmittedInput,
            ("design",),
            completes,
            consumes_credit=False,
            admit=admits.admit,
            server_fields=frozenset({"resolved"}),
        )
    )
    env.app.dependency_overrides[job_kinds] = lambda: registry
    env.app.dependency_overrides[credit_ledger] = lambda: spy
    with engine_for(database_url) as engine:
        admits.admin = engine
        yield admits, spy


def body(browser: Browser, kind: str, **input_: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"mowl_connection_id": str(browser.connection_id)}
    if "class_id" not in input_:
        values["title"] = "Thursday intervals"
    return {"kind": kind, "input": values | input_}


async def create(browser: Browser, kind: str, key: str | None = None, **input_: Any) -> Any:
    return await browser.post(
        "/api/jobs", body(browser, kind, **input_), {"Idempotency-Key": key or new_key()}
    )


async def run_one(jenv: JobsEnv, kind: str) -> Execution:
    """Claim the one queued job of ``kind`` and run its attempt with the seams' registry."""
    registry = KindRegistry()
    registry.register(JobKind(KIND, FreeInput, ("pick", "design"), two_phases))
    registry.register(JobKind(FREE, FreeInput, ("pick", "design"), two_phases))
    registry.register(JobKind(ADMITTED, AdmittedInput, ("design",), completes))
    row = await queue.claim(jenv.factory, "k1-w", jenv.settings.job_lease_seconds, [kind])
    assert row is not None, "nothing to claim"
    execution = Execution(row)
    await execute(
        execution,
        jenv.factory,
        registry,
        RunConfig(lease_seconds=jenv.settings.job_lease_seconds),
        asyncio.Event(),
    )
    return execution


def count(jenv: JobsEnv, table: str, owner: UUID) -> int:
    return jenv.sql(f"SELECT count(*) FROM {table} WHERE owner_id = :o", o=owner).scalar_one()  # noqa: S608


# consumes_credit


async def test_a_kind_that_consumes_no_credit_never_calls_the_ledger(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    """Created with ``credit_state = 'none'``, no ``reserve``; approved without ``allows``;
    ended, settle skips it. The credit kind next to it still reserves, and settles."""
    _, spy = seams
    alice: Browser = await browsers()
    free = await create(alice, FREE)
    assert free.status_code == 201, free.text
    free_id = UUID(free.json()["id"])
    assert jenv.row(free_id).credit_state == "none"
    assert spy.calls == []

    await run_one(jenv, FREE)
    waiting = jenv.row(free_id)
    assert waiting.state == "waiting_for_approval"
    approved = await alice.approve(free_id, waiting.approval_revision_id, LIST_HASH)
    assert (approved.status_code, approved.json()["state"]) == (200, "queued")
    await run_one(jenv, FREE)
    assert jenv.row(free_id).state == "completed"
    assert await settle(jenv.factory, spy) == 0
    assert jenv.row(free_id).credit_state == "none"
    assert spy.calls == []  # no reserve, allows, commit or release, ever

    paid = await create(alice, KIND)
    paid_id = UUID(paid.json()["id"])
    assert (jenv.row(paid_id).credit_state, spy.calls) == ("reserved", [("reserve", paid_id)])
    jenv.sql("UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :i", i=paid_id)
    assert await settle(jenv.factory, spy) == 1
    assert jenv.row(paid_id).credit_state == "released"
    assert spy.calls == [("reserve", paid_id), ("release", paid_id)]


async def test_the_per_user_limit_counts_jobs_of_every_kind(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    admits, _ = seams
    alice: Browser = await browsers()
    assert (await create(alice, KIND)).status_code == 201
    assert (await create(alice, FREE)).status_code == 201  # the limit (2) is now reached
    for kind in (KIND, FREE, ADMITTED):
        response = await create(alice, kind)
        assert (response.status_code, response.json()) == (
            429,
            {"detail": {"reason": "busy", "limit": 2}},
        )
    assert admits.calls == 0  # the limit is counted before admit runs
    assert count(jenv, "jobs", alice.user_id) == 2


# admit


async def test_admit_runs_under_the_owner_lock_and_stores_its_server_fields(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    admits, spy = seams
    alice: Browser = await browsers()
    key = new_key()
    first = await create(alice, ADMITTED, key)
    assert first.status_code == 201, first.text
    job_id = UUID(first.json()["id"])
    assert (admits.calls, admits.locked, admits.inserted) == (1, [True], [job_id])
    row = jenv.row(job_id)
    assert row.input["resolved"] == SERVER_VALUE
    assert AdmittedInput.model_validate(row.input).resolved == SERVER_VALUE  # as the worker reads
    assert row.credit_state == "none"
    assert spy.calls == []
    # The request hash is the browser's body, before admit added anything: a retry matches it.
    sent = AdmittedInput.model_validate(body(alice, ADMITTED)["input"]).model_dump(mode="json")
    want = hashlib.sha256(canonical_json({"kind": ADMITTED, "input": sent})).digest()
    assert bytes(row.request_hash) == want

    replay = await create(alice, ADMITTED, key)
    assert (replay.status_code, replay.json()) == (200, first.json())
    other = await create(alice, ADMITTED, key, want="refuse")
    assert (other.status_code, other.json()) == (
        409, {"detail": {"reason": "idempotency_mismatch"}}
    )  # fmt: skip
    assert admits.calls == 1  # the idempotency lookup comes first: admit never ran again
    assert count(jenv, "jobs", alice.user_id) == 1


async def test_a_refusal_creates_no_job_no_class_and_keeps_nothing_admit_wrote(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    admits, spy = seams
    alice: Browser = await browsers()
    key = new_key()
    refused = await create(alice, ADMITTED, key, want="refuse")
    assert (refused.status_code, refused.json()) == (
        409, {"detail": {"reason": "stale_base", "limit": 3, "count": 3}}
    )  # fmt: skip
    written = await create(alice, ADMITTED, want="write_then_refuse")
    assert (written.status_code, written.json()) == (
        409, {"detail": {"reason": "revision_limit"}}
    )  # fmt: skip
    planted = jenv.sql("SELECT count(*) FROM classes WHERE id = :i", i=admits.planted)
    assert planted.scalar_one() == 0  # the savepoint rolled back what admit wrote
    assert (count(jenv, "jobs", alice.user_id), count(jenv, "classes", alice.user_id)) == (0, 0)
    assert (admits.locked, admits.inserted, spy.calls) == ([True, True], [], [])
    # Nothing was stored under the key, so the same request is admitted (and refused) again.
    again = await create(alice, ADMITTED, key, want="refuse")
    assert again.json() == refused.json()
    assert admits.calls == 3


async def test_two_racing_creations_on_one_class_give_one_job_and_one_class_busy(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    admits, _ = seams
    alice: Browser = await browsers()
    made = await create(alice, FREE)
    class_id = made.json()["class_id"]
    jenv.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :i",
        i=made.json()["id"],
    )
    first, second = await asyncio.gather(
        create(alice, ADMITTED, class_id=class_id), create(alice, ADMITTED, class_id=class_id)
    )
    statuses = sorted((first.status_code, second.status_code))
    assert statuses == [201, 409], (first.text, second.text)
    busy = first if first.status_code == 409 else second
    assert busy.json() == {"detail": {"reason": "class_busy"}}
    assert admits.calls == 1  # the second waited for the lock, then saw the open job
    open_jobs = jenv.sql(
        "SELECT count(*) FROM jobs WHERE class_id = :c AND state = 'queued'", c=class_id
    )
    assert open_jobs.scalar_one() == 1


async def test_a_server_field_from_the_browser_is_refused_unread(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    admits, _ = seams
    alice: Browser = await browsers()
    planted = "PLANTED-VALUE-ssssssssssssssss"
    for value in (planted, None):
        response = await create(alice, ADMITTED, resolved=value)
        assert response.status_code == 422, response.text
        assert planted not in response.text
        assert response.json()["detail"]["reason"] == "invalid_input"
    assert admits.calls == 0
    assert count(jenv, "jobs", alice.user_id) == 0


async def test_a_kind_without_admit_is_created_as_before(
    jenv: JobsEnv, browsers: Any, seams: tuple[Admits, LedgerSpy]
) -> None:
    alice: Browser = await browsers()
    response = await create(alice, KIND)
    assert response.status_code == 201
    row = jenv.row(UUID(response.json()["id"]))
    assert row.input == {
        "class_id": None,
        "title": "Thursday intervals",
        "mowl_connection_id": str(alice.connection_id),
    }


def test_the_kind_declares_its_seams_safely() -> None:
    async def admit(db: Any, ctx: Any, parsed: Any) -> Admitted:
        return Admitted()

    def kind(**extra: Any) -> JobKind:
        return JobKind("k", AdmittedInput, ("s",), completes, **extra)

    assert kind().consumes_credit is True
    assert kind().admit is None
    with pytest.raises(ValueError, match="only admit"):
        kind(server_fields=frozenset({"resolved"}))
    for name in ("nope", "mowl_connection_id"):  # unknown, or one the browser must send
        with pytest.raises(ValueError, match="optional field"):
            kind(admit=admit, server_fields=frozenset({name}))
    made = kind(admit=admit, server_fields=frozenset({"resolved"}))
    parsed = AdmittedInput.model_validate({"title": "t", "mowl_connection_id": str(uuid.uuid4())})
    stored = made.admitted_input(parsed, {"resolved": "x"})
    assert isinstance(stored, AdmittedInput)
    assert stored.resolved == "x"
    with pytest.raises(ValueError, match="server fields"):
        made.admitted_input(parsed, {"want": "refuse"})  # not a server field
    with pytest.raises(ValidationError):
        made.admitted_input(parsed, {"resolved": 7})  # still validated by the model
    broken: tuple[tuple[str, dict[str, Any]], ...] = (
        ("Not A Code", {}), ("ok", {"reason": 1}), ("ok", {"n": "3"}), ("ok", {"n": True}),
    )  # fmt: skip
    for reason, counts in broken:
        with pytest.raises(ValueError, match="refusal"):
            Refused(reason, counts)
