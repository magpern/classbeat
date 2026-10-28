"""Runs end to end with the test-only kind: phases, retries, operation_busy, crash and lease
expiry, settle exactly once (ADR 0005 sections 2 to 5)."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from classbeat.connections import ConnectionFailure
from classbeat.db import user_session
from classbeat.egress import OutboundBlocked
from classbeat.jobs import AwaitApproval, Complete, JobFailure, JobRun, queue, settle, settle_one
from classbeat.jobs.runner import RunConfig, execute
from classbeat.jobs.user import approve
from ctx import make_context
from jobcore import (
    SONG_LIST,
    CountingLedger,
    JobsEnv,
    Upstream,
    claim,
    content_hash,
    registry_for,
    run_next,
    store_draft,
    store_list,
)

pytestmark = pytest.mark.usefixtures("jenv")


def ready(env: JobsEnv, job_id: uuid.UUID) -> None:
    """Backoff is real time; the tests make the requeued job claimable at once."""
    env.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)


async def test_a_retried_pre_approval_phase_waits_is_approved_and_completes(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job()
    upstream = Upstream()

    async def run(job_run: JobRun) -> Complete | AwaitApproval:
        if not job_run.approved:
            await job_run.stage("pick")
            if job_run.attempt == 1:
                raise JobFailure("model_unavailable")
            return AwaitApproval(store_list)
        await job_run.stage("design")
        async with job_run.external_call(10):
            await upstream.call()
        return Complete(store_draft)

    registry = registry_for(run)
    await run_next(jenv, registry)
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 1)
    assert row.available_at > row.updated_at  # backoff
    ready(jenv, job_id)
    await run_next(jenv, registry)
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.leased_by) == ("waiting_for_approval", 2, None)
    assert row.expires_at is not None
    ctx = make_context(owner)
    async with user_session(jenv.factory, ctx) as db:
        result = await approve(db, ctx, job_id, row.approval_revision_id, content_hash(SONG_LIST))
    assert result.accepted
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 0)  # a new phase
    await run_next(jenv, registry)
    row = jenv.row(job_id)
    assert (row.state, row.attempt, row.stage) == ("completed", 1, "design")
    assert upstream.calls == 1
    assert jenv.kinds(job_id) == [
        "claimed",
        "stage_started",
        "retry_scheduled",
        "claimed",
        "stage_started",
        "revision_created",
        "waiting_for_approval",
        "approved",
        "claimed",
        "stage_started",
        "revision_created",
        "completed",
    ]
    seqs = [seq for seq, _, _, _ in jenv.events(job_id)]
    assert seqs == list(range(1, len(seqs) + 1))


async def test_retryable_failures_use_their_attempts_then_fail(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(max_attempts=2)

    async def run(job_run: JobRun) -> Complete:
        raise ConnectionFailure("upstream_unavailable")

    registry = registry_for(run)
    await run_next(jenv, registry)
    assert jenv.row(job_id).state == "queued"
    ready(jenv, job_id)
    await run_next(jenv, registry)
    row = jenv.row(job_id)
    assert (row.state, row.error_category, row.attempt) == ("failed", "upstream_unavailable", 2)
    [*_, (_, kind, payload, _)] = jenv.events(job_id)
    assert (kind, payload) == ("failed", {"category": "upstream_unavailable"})


@pytest.mark.parametrize(
    ("raised", "category"),
    [
        (JobFailure("model_refused"), "model_refused"),
        (ConnectionFailure("needs_reconnect"), "needs_reconnect"),
        (ConnectionFailure("invalid_credentials"), "needs_reconnect"),
        (ConnectionFailure("unknown"), "internal"),
        (OutboundBlocked("evil.example"), "outbound_blocked"),
        (RuntimeError("a secret-looking text sk-ant-123"), "internal"),
    ],
)
async def test_non_retryable_failures_fail_at_once_with_the_category_only(
    jenv: JobsEnv, raised: Exception, category: str, caplog: pytest.LogCaptureFixture
) -> None:
    _, job_id = jenv.job()

    async def run(job_run: JobRun) -> Complete:
        raise raised

    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", category)
    assert "sk-ant-123" not in caplog.text
    assert "sk-ant-123" not in str(jenv.events(job_id))


async def test_a_rate_limit_waits_at_least_its_hint(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()

    async def run(job_run: JobRun) -> Complete:
        raise ConnectionFailure("rate_limited", retry_after=1200)

    await run_next(jenv, registry_for(run))
    wait = jenv.sql(
        "SELECT extract(epoch FROM available_at - now()) FROM jobs WHERE id = :id", id=job_id
    ).scalar_one()
    assert 1190 < wait <= 1200


async def test_invalid_input_and_an_unknown_kind_fail_without_running(jenv: JobsEnv) -> None:
    _, bad = jenv.job(input='{"unexpected": 1}')
    calls: list[int] = []

    async def run(job_run: JobRun) -> Complete:
        calls.append(1)
        return Complete()

    await run_next(jenv, registry_for(run))
    assert (jenv.row(bad).state, jenv.row(bad).error_category) == ("failed", "invalid_input")
    assert calls == []


async def test_a_failing_store_rolls_back_and_fails_internal(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()

    async def broken(fs):
        await store_draft(fs)
        raise ValueError("bug")

    async def run(job_run: JobRun) -> Complete:
        return Complete(broken)

    await run_next(jenv, registry_for(run))
    assert (jenv.row(job_id).state, jenv.row(job_id).error_category) == ("failed", "internal")
    assert jenv.counts(job_id)[1] == 0  # the revision was rolled back with the store


async def test_a_database_blip_while_storing_retries_the_attempt(jenv: JobsEnv) -> None:
    from sqlalchemy.exc import OperationalError

    _, job_id = jenv.job()

    async def blip(fs):
        await store_draft(fs)
        raise OperationalError("COMMIT", {}, ConnectionError())

    async def run(job_run: JobRun) -> Complete:
        return Complete(blip)

    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 1)
    [*_, (_, kind, payload, _)] = jenv.events(job_id)
    assert (kind, payload["category"]) == ("retry_scheduled", "upstream_unavailable")
    assert jenv.counts(job_id)[1] == 0  # the revision was rolled back


async def test_operation_busy_requeues_without_an_attempt_up_to_the_cap(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(max_attempts=2)

    async def run(job_run: JobRun) -> Complete:
        raise JobFailure("operation_busy")

    registry = registry_for(run)
    config = RunConfig(busy_requeue_limit=3)
    for _ in range(3):  # free requeues
        await run_next(jenv, registry, config=config)
        row = jenv.row(job_id)
        assert (row.state, row.attempt) == ("queued", 0)
        ready(jenv, job_id)
    await run_next(jenv, registry, config=config)  # the cap: this one counts
    assert (jenv.row(job_id).state, jenv.row(job_id).attempt) == ("queued", 1)
    ready(jenv, job_id)
    await run_next(jenv, registry, config=config)
    row = jenv.row(job_id)
    assert (row.state, row.error_category, row.attempt) == ("failed", "operation_busy", 2)
    busy = [p for _, k, p, _ in jenv.events(job_id) if k == "retry_scheduled"]
    assert len(busy) == 4
    assert all(p["category"] == "operation_busy" for p in busy)


async def test_a_crash_mid_stage_is_reaped_and_the_retry_completes(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()
    started = asyncio.Event()

    async def hang(job_run: JobRun) -> Complete:
        await job_run.stage("pick")
        started.set()
        await asyncio.sleep(60)
        return Complete()

    execution = await claim(jenv)
    task = asyncio.ensure_future(
        execute(execution, jenv.factory, registry_for(hang), RunConfig(), asyncio.Event())
    )
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()  # the worker dies: no hand-back, nothing recorded
    with pytest.raises(asyncio.CancelledError):
        await task
    assert jenv.row(job_id).state == "running"
    jenv.expire_lease(job_id)
    assert await queue.reap(jenv.factory) == 1
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 1)  # the crashed attempt counts
    ready(jenv, job_id)

    async def finish(job_run: JobRun) -> Complete:
        await job_run.stage("pick")
        return Complete(store_draft)

    await run_next(jenv, registry_for(finish))
    row = jenv.row(job_id)
    assert (row.state, row.attempt) == ("completed", 2)


async def test_a_crash_mid_external_call_leaves_it_pending_under_the_old_token(
    jenv: JobsEnv,
) -> None:
    owner, job_id = jenv.job()
    in_call = asyncio.Event()
    operation = uuid.uuid4()

    async def call(job_run: JobRun) -> Complete:
        async with job_run.external_call(30):
            async with job_run.fenced() as fs:  # X1's pending write extends the lease
                await fs.db.execute(
                    text(
                        "INSERT INTO external_operations (id, owner_id, job_id, kind, "
                        "idempotency_key, request_hash, state, lease_version) VALUES (:id, :o, "
                        ":j, 'mowl_attach_spotify_playlist', 'spotify:37i9dQZF1DXcBWIGoYBM5M', "
                        "decode(repeat('ab', 32), 'hex'), 'pending', :v)"
                    ),
                    {"id": operation, "o": owner, "j": job_id, "v": job_run.lease.lease_version},
                )
                await fs.extend_lease_for_call(30)
            in_call.set()
            await asyncio.sleep(60)  # the provider call
        return Complete()

    execution = await claim(jenv)
    task = asyncio.ensure_future(
        execute(execution, jenv.factory, registry_for(call), RunConfig(), asyncio.Event())
    )
    await asyncio.wait_for(in_call.wait(), 5)
    lease = jenv.sql(
        "SELECT extract(epoch FROM lease_expires_at - now()) FROM jobs WHERE id = :id", id=job_id
    ).scalar_one()
    assert lease > 55  # the call's 30 s plus 30 s
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    jenv.expire_lease(job_id)
    assert await queue.reap(jenv.factory) == 1
    op = jenv.sql(
        "SELECT state, lease_version FROM external_operations WHERE id = :id", id=operation
    ).one()
    assert (op.state, op.lease_version) == ("pending", 1)
    assert jenv.row(job_id).lease_version == 2  # the next holder sees a stale pending row


async def test_lease_expiry_with_attempts_spent_fails_and_releases_credit_once(
    jenv: JobsEnv,
) -> None:
    owner, job_id = jenv.job(max_attempts=1)
    await claim(jenv)
    jenv.expire_lease(job_id)
    assert await queue.reap(jenv.factory) == 1
    row = jenv.row(job_id)
    assert (row.state, row.error_category, row.credit_state) == ("failed", "lease_lost", "reserved")
    ledger = CountingLedger()
    results = await asyncio.gather(
        *(settle_one(jenv.factory, ledger, owner, job_id) for _ in range(5))
    )
    assert sorted(r or "" for r in results) == ["", "", "", "", "released"]
    assert await settle(jenv.factory, ledger) == 0
    assert ledger.calls == [("release", job_id)]
    assert jenv.row(job_id).credit_state == "released"


async def test_settle_commits_completed_and_releases_the_rest_exactly_once(jenv: JobsEnv) -> None:
    _, done = jenv.job()

    async def run(job_run: JobRun) -> Complete:
        return Complete(store_draft)

    await run_next(jenv, registry_for(run))
    _, cancelled = jenv.job(state="cancelled", finished_at=datetime.now(UTC))
    _, open_job = jenv.job()
    ledger = CountingLedger()
    assert await settle(jenv.factory, ledger) == 2
    assert await settle(jenv.factory, ledger) == 0
    assert sorted(ledger.calls) == sorted([("commit", done), ("release", cancelled)])
    assert jenv.row(open_job).credit_state == "reserved"
