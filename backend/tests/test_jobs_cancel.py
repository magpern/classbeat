"""Cancellation and approval (ADR 0005 sections 2 and 4)."""

import asyncio
import uuid

import pytest

from classbeat.db import user_session
from classbeat.jobs import AwaitApproval, Complete, JobFailure, JobRun, queue, settle
from classbeat.jobs.runner import RunConfig, execute
from classbeat.jobs.user import Approval, approve, cancel
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


async def user_cancel(env: JobsEnv, owner: uuid.UUID, job_id: uuid.UUID) -> str | None:
    ctx = make_context(owner)
    async with user_session(env.factory, ctx) as db:
        return await cancel(db, ctx, job_id)


async def user_approve(
    env: JobsEnv, owner: uuid.UUID, job_id: uuid.UUID, revision: uuid.UUID, digest: bytes
) -> Approval:
    ctx = make_context(owner)
    async with user_session(env.factory, ctx) as db:
        return await approve(db, ctx, job_id, revision, digest)


async def waiting_job(env: JobsEnv) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    owner, job_id = env.job()

    async def run(job_run: JobRun) -> AwaitApproval:
        return AwaitApproval(store_list)

    await run_next(env, registry_for(run))
    row = env.row(job_id)
    assert row.state == "waiting_for_approval"
    return owner, job_id, row.approval_revision_id


async def test_queued_and_waiting_jobs_cancel_at_once_and_idempotently(jenv: JobsEnv) -> None:
    owner, queued = jenv.job()
    assert await user_cancel(jenv, owner, queued) == "cancelled"
    owner2, waiting, _ = await waiting_job(jenv)
    assert await user_cancel(jenv, owner2, waiting) == "cancelled"
    for o, j in ((owner, queued), (owner2, waiting)):
        assert jenv.row(j).finished_at is not None
        assert await user_cancel(jenv, o, j) == "cancelled"
        assert jenv.kinds(j).count("cancelled") == 1  # no second event
    assert await user_cancel(jenv, owner, waiting) is None  # another user's job: not found


async def test_a_running_job_stops_at_its_next_checkpoint_without_another_call(
    jenv: JobsEnv,
) -> None:
    owner, job_id = jenv.job()
    upstream = Upstream()
    turn_done = asyncio.Event()
    cancelled = asyncio.Event()

    async def run(job_run: JobRun) -> Complete:
        for _ in range(5):  # model turns and tool calls, each behind a checkpoint
            await job_run.checkpoint()
            await upstream.call()
            if upstream.calls == 2:
                turn_done.set()
                await cancelled.wait()
        return Complete(store_draft)

    execution = await claim(jenv)
    task = asyncio.ensure_future(
        execute(execution, jenv.factory, registry_for(run), RunConfig(), asyncio.Event())
    )
    await asyncio.wait_for(turn_done.wait(), 5)
    assert await user_cancel(jenv, owner, job_id) == "running"
    assert await user_cancel(jenv, owner, job_id) == "running"  # requested once
    cancelled.set()
    await asyncio.wait_for(task, 5)
    row = jenv.row(job_id)
    assert (row.state, row.finished_at is not None) == ("cancelled", True)
    assert upstream.calls == 2
    assert jenv.kinds(job_id) == ["claimed", "cancel_requested", "cancelled"]
    assert jenv.counts(job_id)[1] == 0


async def test_no_external_call_starts_after_a_cancel(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job()
    upstream = Upstream()

    async def run(job_run: JobRun) -> Complete:
        await user_cancel(jenv, owner, job_id)
        async with job_run.external_call(10):
            await upstream.call()
        return Complete()

    await run_next(jenv, registry_for(run))
    assert jenv.row(job_id).state == "cancelled"
    assert upstream.calls == 0


async def test_a_cancel_racing_completion_ends_in_exactly_one_terminal_state(
    jenv: JobsEnv,
) -> None:
    for _ in range(6):
        owner, job_id = jenv.job()
        go = asyncio.Event()

        async def run(job_run: JobRun, go: asyncio.Event = go) -> Complete:
            await go.wait()
            return Complete(store_draft)

        execution = await claim(jenv)
        task = asyncio.ensure_future(
            execute(execution, jenv.factory, registry_for(run), RunConfig(), asyncio.Event())
        )
        go.set()
        await asyncio.gather(task, user_cancel(jenv, owner, job_id))
        row = jenv.row(job_id)
        assert row.state in ("completed", "cancelled")
        terminal = [k for k in jenv.kinds(job_id) if k in ("completed", "cancelled", "failed")]
        assert len(terminal) == 1
        assert (jenv.counts(job_id)[1] == 1) == (row.state == "completed")


async def test_a_retry_of_a_job_with_a_cancel_requested_cancels_it(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job()

    async def run(job_run: JobRun) -> Complete:
        await user_cancel(jenv, owner, job_id)
        raise JobFailure("upstream_unavailable")

    await run_next(jenv, registry_for(run))
    assert jenv.row(job_id).state == "cancelled"


async def test_approval_refusals_start_nothing(jenv: JobsEnv) -> None:
    owner, job_id, revision = await waiting_job(jenv)
    good = content_hash(SONG_LIST)
    before = jenv.kinds(job_id)
    stranger = jenv.user()
    assert (await user_approve(jenv, stranger, job_id, revision, good)).reason == "not_found"
    assert (await user_approve(jenv, owner, job_id, revision, b"x" * 32)).reason == "hash_mismatch"
    other = uuid.uuid4()
    assert (await user_approve(jenv, owner, job_id, other, good)).reason == "superseded"
    jenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) SELECT gen_random_uuid(), owner_id, class_id, 99, "
        "'song_list', '{}', decode(repeat('00', 32), 'hex'), '{}', '{}' FROM jobs WHERE id = :id",
        id=job_id,
    )
    assert (await user_approve(jenv, owner, job_id, revision, good)).reason == "superseded"
    assert jenv.kinds(job_id) == before
    assert jenv.row(job_id).state == "waiting_for_approval"


async def test_a_repeated_approval_returns_the_state_without_a_second_event(
    jenv: JobsEnv,
) -> None:
    owner, job_id, revision = await waiting_job(jenv)
    good = content_hash(SONG_LIST)
    first = await user_approve(jenv, owner, job_id, revision, good)
    again = await user_approve(jenv, owner, job_id, revision, good)
    assert (first.accepted, first.state) == (True, "queued")
    assert (again.accepted, again.state) == (True, "queued")
    assert jenv.kinds(job_id).count("approved") == 1
    assert (await user_approve(jenv, owner, job_id, revision, b"y" * 32)).reason == "not_waiting"
    _, queued = jenv.job(owner)
    assert (await user_approve(jenv, owner, queued, revision, good)).reason == "not_waiting"


async def test_expiry_refuses_the_approval_and_releases_the_credit(jenv: JobsEnv) -> None:
    owner, job_id, revision = await waiting_job(jenv)
    jenv.sql("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :id", id=job_id)
    good = content_hash(SONG_LIST)
    assert (await user_approve(jenv, owner, job_id, revision, good)).reason == "expired"
    assert await queue.expire(jenv.factory) == 1
    assert jenv.row(job_id).state == "expired"
    assert (await user_approve(jenv, owner, job_id, revision, good)).reason == "not_waiting"
    ledger = CountingLedger()
    assert await settle(jenv.factory, ledger) == 1
    assert ledger.calls == [("release", job_id)]


async def test_a_job_waits_for_approval_at_most_once(jenv: JobsEnv) -> None:
    owner, job_id, revision = await waiting_job(jenv)
    assert (await user_approve(jenv, owner, job_id, revision, content_hash(SONG_LIST))).accepted

    async def run(job_run: JobRun) -> AwaitApproval:
        return AwaitApproval(store_list)  # a second wait is a bug

    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "internal")


async def test_the_database_also_refuses_a_second_wait(jenv: JobsEnv) -> None:
    """Past the Python check: the wait's own UPDATE carries ``approved_at IS NULL``."""
    from datetime import UTC, datetime

    from classbeat.jobs import fenced_session, transitions
    from classbeat.jobs.issuer import issue

    _, job_id = jenv.job(approved_at=datetime.now(UTC))
    lease = (await issue(jenv.factory, (await claim(jenv)).claimed)).lease
    with pytest.raises(transitions.WaitedBefore):
        async with fenced_session(jenv.factory, lease) as fs:
            await transitions.wait_for_approval(fs, uuid.uuid4(), b"z" * 32, 1)
    assert jenv.row(job_id).state == "running"
