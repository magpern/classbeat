"""The jobs API: approve (ADR 0005 section 2; #87, J3). Every check of the ADR, the owner lock
before the per-user limit is counted, and that no refused approval starts a stage. The waiting
jobs are made through the API and the worker side with the test-only kind; no provider call."""

import asyncio
import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from authtools import Env
from classbeat import connections
from classbeat.context import UserContext
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.credits import UnmeteredLedger
from classbeat.jobs.routes import credit_ledger, job_kinds
from ctx import make_context
from jobapi import (
    FAKE_TOKEN,
    LIST_HASH,
    Barrier,
    Browser,
    api_registry,
    owner_lock_key,
    plant_running,
    until_waiting_on_the_owner_lock,
    waiting_job,
)
from jobcore import KIND, JobsEnv, run_next
from jobtools import digest, engine_for

pytestmark = pytest.mark.usefixtures("jenv")


def refused(response: Any, reason: str, status: int = 409) -> None:
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})


async def nothing_started(jenv: JobsEnv, job_id: UUID, kinds: list[str]) -> None:
    row = jenv.row(job_id)
    assert (row.state, row.approved_at) == ("waiting_for_approval", None)
    assert jenv.kinds(job_id) == kinds
    assert await queue.claim(jenv.factory, "w", 60, [KIND]) is None


async def test_approval_runs_the_next_phase_and_a_repeat_writes_nothing(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    approved = await alice.approve(job_id, revision, LIST_HASH)
    assert approved.status_code == 200, approved.text
    assert (approved.json()["state"], approved.json()["approval_revision_id"]) == (
        "queued", str(revision)
    )  # fmt: skip
    row = jenv.row(job_id)
    assert (row.attempt, row.approved_at is not None) == (0, True)
    again = await alice.approve(job_id, revision, LIST_HASH)
    assert (again.status_code, again.json()["state"]) == (200, "queued")
    assert jenv.kinds(job_id).count("approved") == 1
    await run_next(jenv, api_registry())
    assert jenv.row(job_id).state == "completed"
    after = await alice.approve(job_id, revision, LIST_HASH)
    assert (after.status_code, after.json()["state"]) == (200, "completed")
    refused(await alice.approve(job_id, revision, "1" * 64), "not_waiting")


async def test_every_refusal_starts_nothing(jenv: JobsEnv, browsers: Any, env: Env) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    kinds = jenv.kinds(job_id)

    refused(await alice.approve(job_id, revision, "0" * 64), "hash_mismatch")
    refused(await alice.approve(job_id, uuid.uuid4(), LIST_HASH), "superseded")
    malformed = await alice.approve(job_id, revision, "ZZ")
    assert malformed.status_code == 422
    await nothing_started(jenv, job_id, kinds)

    # The connection: needs reconnect, then gone.
    jenv.sql(
        "UPDATE connections SET status = 'needs_reconnect' WHERE id = :i", i=alice.connection_id
    )
    refused(await alice.approve(job_id, revision, LIST_HASH), "not_connected")
    jenv.sql("DELETE FROM connections WHERE id = :i", i=alice.connection_id)
    refused(await alice.approve(job_id, revision, LIST_HASH), "not_connected")
    await nothing_started(jenv, job_id, kinds)


async def test_a_reconnected_account_with_a_new_row_is_not_the_jobs_connection(
    jenv: JobsEnv, browsers: Any, env: Env
) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    jenv.sql("DELETE FROM connections WHERE id = :i", i=alice.connection_id)
    await connections.save(
        env.factory,
        env.app.state.keyring,
        make_context(alice.user_id),
        "mowl",
        {"token": FAKE_TOKEN},
    )
    refused(await alice.approve(job_id, revision, LIST_HASH), "not_connected")
    await nothing_started(jenv, job_id, jenv.kinds(job_id))


async def test_superseded_by_a_newer_list_and_expired(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    kinds = jenv.kinds(job_id)
    row = jenv.row(job_id)
    newer = uuid.uuid4()
    jenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 99, 'song_list', "
        "CAST(:content AS jsonb), :h, '{}', '{}')",
        id=newer, o=alice.user_id, c=row.class_id, content=json.dumps({"songs": []}),
        h=digest("newer"),
    )  # fmt: skip
    refused(await alice.approve(job_id, revision, LIST_HASH), "superseded")
    jenv.sql("DELETE FROM class_revisions WHERE id = :i", i=newer)
    jenv.sql("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :i", i=job_id)
    refused(await alice.approve(job_id, revision, LIST_HASH), "expired")
    await nothing_started(jenv, job_id, kinds)
    # Once the sweep moved the job to `expired` the reason stays `expired` (#115 item 1).
    assert jenv.sql("SELECT public.queue_expire(10)").scalar_one() >= 1
    assert jenv.row(job_id).state == "expired"
    refused(await alice.approve(job_id, revision, LIST_HASH), "expired")


async def test_the_per_user_limit_and_the_credit_seam(
    jenv: JobsEnv, browsers: Any, env: Env
) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    kinds = jenv.kinds(job_id)
    first, second = plant_running(jenv, alice.user_id), plant_running(jenv, alice.user_id)
    refused(await alice.approve(job_id, revision, LIST_HASH), "busy", 429)
    jenv.sql("DELETE FROM jobs WHERE id IN (:a, :b)", a=first, b=second)

    class Refusing(UnmeteredLedger):
        async def allows(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> bool:
            return False

    env.app.dependency_overrides[credit_ledger] = Refusing
    refused(await alice.approve(job_id, revision, LIST_HASH), "quota")
    env.app.dependency_overrides[job_kinds] = KindRegistry  # a kind the web no longer knows
    refused(await alice.approve(job_id, revision, LIST_HASH), "invalid_input")
    await nothing_started(jenv, job_id, kinds)


async def test_approval_decides_on_the_job_rows_credit_state(
    jenv: JobsEnv, browsers: Any, env: Env
) -> None:
    """The ledger is asked for what the job row reserved (``credit_state``), not for what the
    kind says today: a refusing ledger stops a reserved job and does not stop one with none."""

    class Refusing(UnmeteredLedger):
        async def allows(self, db: AsyncSession, ctx: UserContext, job_id: UUID) -> bool:
            return False

    env.app.dependency_overrides[credit_ledger] = Refusing
    alice: Browser = await browsers()
    reserved, reserved_rev = await waiting_job(jenv, alice)
    assert jenv.sql("SELECT credit_state FROM jobs WHERE id = :i", i=reserved).scalar_one() == (
        "reserved"
    )
    refused(await alice.approve(reserved, reserved_rev, LIST_HASH), "quota")
    free, free_rev = await waiting_job(jenv, alice)
    jenv.sql("UPDATE jobs SET credit_state = 'none' WHERE id = :i", i=free)
    done = await alice.approve(free, free_rev, LIST_HASH)
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "queued"


async def test_two_concurrent_approvals_cannot_pass_the_limit(
    jenv: JobsEnv, browsers: Any, env: Env
) -> None:
    """Both approvals are held after counting the limit (``Barrier``) until the other gets there:
    without the owner lock both would count one active job and pass (#115 item 5)."""
    alice: Browser = await browsers()
    one, one_rev = await waiting_job(jenv, alice)
    two, two_rev = await waiting_job(jenv, alice)
    plant_running(jenv, alice.user_id)  # one slot of two left
    barrier = Barrier()
    env.app.dependency_overrides[credit_ledger] = lambda: barrier
    responses = await asyncio.gather(
        alice.approve(one, one_rev, LIST_HASH), alice.approve(two, two_rev, LIST_HASH)
    )
    assert (barrier.arrived, barrier.together) == (1, False)  # the second was refused as busy
    assert sorted(r.status_code for r in responses) == [200, 429]
    states = sorted(jenv.row(j).state for j in (one, two))
    assert states == ["queued", "waiting_for_approval"]


async def test_an_approval_racing_a_create_cannot_pass_the_limit(
    jenv: JobsEnv, browsers: Any, env: Env
) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    plant_running(jenv, alice.user_id)  # one slot of two left
    barrier = Barrier()
    env.app.dependency_overrides[credit_ledger] = lambda: barrier
    approved, created = await asyncio.gather(
        alice.approve(job_id, revision, LIST_HASH), alice.create()
    )
    assert (barrier.arrived, barrier.together) == (1, False)
    assert sorted((approved.status_code, created.status_code)) in ([200, 429], [201, 429])
    active = jenv.sql(
        "SELECT count(*) FROM jobs WHERE owner_id = :o AND state IN ('queued', 'running')",
        o=alice.user_id,
    ).scalar()
    assert active == 2


async def test_approve_counts_the_limit_only_after_the_owner_lock(
    jenv: JobsEnv, browsers: Any, database_url: str
) -> None:
    """While another transaction holds the owner's lock (a create in flight), an approval waits;
    the job that transaction commits is then counted, and the approval is refused."""
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    plant_running(jenv, alice.user_id)
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(alice.user_id)},
        )
        task = asyncio.create_task(alice.approve(job_id, revision, LIST_HASH))
        await until_waiting_on_the_owner_lock(jenv)
        assert not task.done()
        plant_running(jenv, alice.user_id)  # the limit (2) is now reached
        holder.commit()
        response = await task
    refused(response, "busy", 429)
    assert jenv.row(job_id).state == "waiting_for_approval"
