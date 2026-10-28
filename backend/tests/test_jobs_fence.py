"""The issuer, the fence, the heartbeat and the stale worker (ADR 0005 section 3)."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from classbeat.db import user_session
from classbeat.jobs import (
    Complete,
    FencedSession,
    JobRun,
    LeaseLost,
    fenced_session,
    queue,
    transitions,
)
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.jobs.issuer import issue
from classbeat.jobs.run import RunStopped
from classbeat.jobs.runner import Execution, RunConfig, execute
from jobcore import KIND, JobsEnv, claim, registry_for, run_next, store_draft
from jobtools import digest

pytestmark = pytest.mark.usefixtures("jenv")


async def _lease(env: JobsEnv, execution: Execution):
    issued = await issue(env.factory, execution.claimed)
    return issued.lease


async def test_the_issuer_builds_a_user_context_from_the_claimed_row_only(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job()
    jenv.sql("UPDATE users SET role = 'admin' WHERE id = :id", id=owner)
    connection_id = uuid.uuid4()
    jenv.sql(
        "INSERT INTO connections (id, owner_id, provider, status, secret, secret_nonce, "
        "wrapped_dek, dek_nonce, key_id) VALUES (:id, :o, 'mowl', 'connected', "
        "decode('00', 'hex'), decode(repeat('00', 12), 'hex'), decode(repeat('00', 48), 'hex'), "
        "decode(repeat('00', 12), 'hex'), 'kek-1')",
        id=connection_id,
        o=owner,
    )
    execution = await claim(jenv)
    issued = await issue(jenv.factory, execution.claimed)
    ctx = issued.lease.ctx
    assert issued.active
    assert (ctx.user_id, ctx.role, ctx.entitlements) == (owner, "user", frozenset())
    assert ctx.connections == frozenset({("mowl", connection_id)})
    assert (issued.lease.job_id, issued.lease.lease_version) == (job_id, 1)


async def test_a_disabled_owner_fails_the_job_with_account_disabled(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job()
    jenv.sql("UPDATE users SET disabled_at = now() WHERE id = :id", id=owner)
    calls: list[int] = []

    async def run(job_run: JobRun) -> Complete:
        calls.append(1)
        return Complete()

    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "account_disabled")
    assert calls == []
    assert jenv.kinds(job_id)[-1] == "failed"


async def test_the_fence_refuses_a_wrong_token_and_rolls_back(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()
    lease = await _lease(jenv, await claim(jenv))
    async with fenced_session(jenv.factory, lease) as fs:
        seq = await fs.event("stage_started", {"stage": "pick"})
    assert seq == 2  # claimed was 1
    stale = type(lease)(lease.job_id, lease.lease_version - 1, lease.ctx)
    with pytest.raises(LeaseLost):
        async with fenced_session(jenv.factory, stale) as fs:
            pass
    other = type(lease)(uuid.uuid4(), lease.lease_version, lease.ctx)
    with pytest.raises(LeaseLost):
        async with fenced_session(jenv.factory, other):
            pass

    async def write_then_lose() -> None:
        async with fenced_session(jenv.factory, lease) as fs:
            await fs.event("stage_started", {"stage": "design"})
            raise LeaseLost  # what a later fenced statement raises: everything rolls back

    with pytest.raises(LeaseLost):
        await write_then_lose()
    assert jenv.kinds(job_id) == ["claimed", "stage_started"]


async def test_event_payloads_are_flat_and_kinds_are_known(jenv: JobsEnv) -> None:
    jenv.job()
    lease = await _lease(jenv, await claim(jenv))
    for kind, payload in (
        ("nope", {}),
        ("failed", {"x": {"nested": 1}}),
        ("failed", {"x": "y" * 101}),
    ):
        with pytest.raises(ValueError, match="event"):
            async with fenced_session(jenv.factory, lease) as fs:
                await fs.event(kind, payload)


async def test_stale_worker_writes_nothing_after_the_reaper_and_b_completes(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job()
    lease_a = await _lease(jenv, await claim(jenv, "worker-a"))
    async with fenced_session(jenv.factory, lease_a) as fs:
        await transitions.set_stage(fs, "pick")
    operation = uuid.uuid4()
    jenv.sql(
        "INSERT INTO external_operations (id, owner_id, job_id, kind, idempotency_key, "
        "request_hash, state, lease_version) VALUES (:id, :o, :j, "
        "'mowl_attach_spotify_playlist', 'spotify:37i9dQZF1DXcBWIGoYBM5M', :h, 'pending', 1)",
        id=operation,
        o=owner,
        j=job_id,
        h=digest("attach"),
    )
    jenv.expire_lease(job_id)
    assert await queue.reap(jenv.factory) == 1
    jenv.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    execution_b = await claim(jenv, "worker-b")
    lease_b = await _lease(jenv, execution_b)
    assert lease_b.lease_version == 3  # claim 1, reap 2, claim 3
    before = (jenv.row(job_id), jenv.counts(job_id))

    agent_run = text(
        "INSERT INTO agent_runs (id, owner_id, job_id, stage, attempt, provider, model, effort, "
        "prompt_version) VALUES (:id, :o, :j, 'design', 1, 'fake', 'm', 'medium', 'v1')"
    )
    outcome = text(
        "UPDATE external_operations SET state = 'succeeded', external_id = 'x' "
        "WHERE id = :id AND state = 'pending' AND job_id = :j AND lease_version = 1"
    )
    writes = {  # every write A could try: event, revision, agent_runs, outcome, state, heartbeat
        "event": lambda fs: fs.event("stage_started", {"stage": "design"}),
        "revision": store_draft,
        "agent_runs": lambda fs: fs.db.execute(
            agent_run, {"id": uuid.uuid4(), "o": owner, "j": job_id}
        ),
        "outcome": lambda fs: fs.db.execute(outcome, {"id": operation, "j": job_id}),
        "complete": lambda fs: transitions.complete(fs, None),
        "fail": lambda fs: transitions.fail(fs, "internal"),
        "retry": lambda fs: transitions.retry(fs, "upstream_unavailable", 0),
        "hand_back": transitions.hand_back,
        "wait": lambda fs: transitions.wait_for_approval(fs, uuid.uuid4(), digest("x"), 1),
        "heartbeat": lambda fs: fs.extend_lease(600, heartbeat=True),
    }
    for name, write in writes.items():
        with pytest.raises(LeaseLost):
            async with fenced_session(jenv.factory, lease_a) as fs:
                await write(fs)
        if name in ("event", "complete", "fail", "retry", "hand_back", "wait", "heartbeat"):
            # Past the fence too: each of these statements carries the fence itself.
            with pytest.raises(LeaseLost):
                async with user_session(jenv.factory, lease_a.ctx) as db:
                    await write(FencedSession(db, lease_a, False))
    run_a = JobRun(
        jenv.factory,
        lease_a,
        kind=KIND,
        input=None,  # pyright: ignore[reportArgumentType]
        attempt=1,
        approved=False,
        lease_seconds=30,
        shutdown=asyncio.Event(),
    )
    run_a.lease_seconds = 0.03
    await asyncio.wait_for(run_a.heartbeat(), timeout=5)  # returns once the fence fails
    assert run_a.lost
    with pytest.raises(RunStopped):
        await run_a.checkpoint()
    after = (jenv.row(job_id), jenv.counts(job_id))
    assert after == before
    assert (
        jenv.sql("SELECT state FROM external_operations WHERE id = :id", id=operation).scalar_one()
        == "pending"
    )

    async def finish(job_run: JobRun) -> Complete:
        await job_run.stage("design")
        return Complete(store_draft)

    await execute(execution_b, jenv.factory, registry_for(finish), RunConfig(), asyncio.Event())
    assert jenv.row(job_id).state == "completed"
    assert {v for _, _, _, v in jenv.events(job_id)[-3:]} == {3}


async def test_the_heartbeat_extends_with_greatest_and_sees_a_cancel(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()
    lease = await _lease(jenv, await claim(jenv))
    async with fenced_session(jenv.factory, lease) as fs:
        await fs.extend_lease_for_call(500)  # a pending external call
    remaining = "SELECT extract(epoch FROM lease_expires_at - now()) FROM jobs WHERE id = :id"
    assert jenv.sql(remaining, id=job_id).scalar_one() > 500 + CALL_LEASE_MARGIN_SECONDS - 5
    async with fenced_session(jenv.factory, lease) as fs:
        assert await fs.extend_lease(60, heartbeat=True) is False
    assert jenv.sql(remaining, id=job_id).scalar_one() > 500  # not shortened
    jenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id", id=job_id)
    run = JobRun(
        jenv.factory,
        lease,
        kind=KIND,
        input=None,  # pyright: ignore[reportArgumentType]
        attempt=1,
        approved=False,
        lease_seconds=30,
        shutdown=asyncio.Event(),
    )
    run.lease_seconds = 0.06
    task = asyncio.ensure_future(run.heartbeat())
    for _ in range(100):
        await asyncio.sleep(0.02)
        if run.cancel_requested:
            break
    task.cancel()
    assert run.cancel_requested
    assert not run.lost
    assert jenv.sql(remaining, id=job_id).scalar_one() > 500


async def test_a_lost_lease_abandons_the_pipeline_but_not_a_call_in_flight(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()
    reached: list[str] = []
    gate = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        job_run.lease_seconds = 0.06
        async with job_run.external_call(5):
            jenv.sql("UPDATE jobs SET lease_version = lease_version + 1 WHERE id = :id", id=job_id)
            await gate.wait()  # the heartbeat finds the lease gone meanwhile
            reached.append("call returned")
        await asyncio.sleep(5)  # abandoned here
        reached.append("after")
        return Complete()

    async def release() -> None:
        for _ in range(200):
            await asyncio.sleep(0.02)
            if execution.run is not None and execution.run.lost:
                break
        gate.set()

    execution = await claim(jenv)
    releaser = asyncio.ensure_future(release())
    await asyncio.wait_for(
        execute(execution, jenv.factory, registry_for(run), RunConfig(), asyncio.Event()), 5
    )
    await releaser
    assert reached == ["call returned"]
    assert (execution.result, execution.settled) == ("lost", True)
    assert jenv.row(job_id).state == "running"  # the reaper's, once the lease expires
