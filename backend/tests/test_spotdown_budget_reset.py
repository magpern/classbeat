"""When a job's spotdown wait budget and outage flag reset (ADR 0040 amendment "spotdown is the
only analysis path", item 4; #1352, #1354): a retryable ending and Approve reset them; a
lease-lost requeue, ``hand_back`` and ``operation_busy`` keep them; only the job's own row.
Needs the test database."""

import uuid
from typing import Any

import pytest

from classbeat.jobs import Complete, JobFailure, JobRun, queue, transitions
from classbeat.jobs.errors import RETRYABLE
from classbeat.jobs.fence import fenced_session
from classbeat.jobs.issuer import issue
from jobcore import JobsEnv, claim, registry_for, run_next

pytestmark = pytest.mark.usefixtures("jenv")

SPENT = (123.0, True)


def plant(env: JobsEnv, owner: uuid.UUID, job_id: uuid.UUID) -> None:
    env.sql(
        "INSERT INTO spotdown_song_budgets (owner_id, job_id, spent_seconds, fallback) "
        "VALUES (:o, :j, :s, :f)",
        o=owner,
        j=job_id,
        s=SPENT[0],
        f=SPENT[1],
    )


def budget(env: JobsEnv, job_id: uuid.UUID) -> tuple[float, bool] | None:
    row = env.sql(
        "SELECT spent_seconds, fallback FROM spotdown_song_budgets WHERE job_id = :j", j=job_id
    ).first()
    return None if row is None else (float(row.spent_seconds), bool(row.fallback))


def fails_with(category: str) -> Any:
    async def run(job_run: JobRun) -> Complete:
        raise JobFailure(category)

    return registry_for(run)


@pytest.mark.parametrize("category", sorted(RETRYABLE))
async def test_a_retryable_ending_resets_the_budget_and_the_flag(
    jenv: JobsEnv, category: str
) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    await run_next(jenv, fails_with(category))
    assert jenv.row(job_id).state == "queued"  # a new attempt follows...
    assert budget(jenv, job_id) == (0.0, False)  # ...with a fresh budget


async def test_the_last_attempt_ends_the_job_and_nothing_is_reset(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=1)
    plant(jenv, owner, job_id)
    await run_next(jenv, fails_with("analysis_unavailable"))
    assert jenv.row(job_id).state == "failed"
    assert budget(jenv, job_id) == SPENT  # no new attempt: no fresh budget


async def test_an_ending_that_is_not_retryable_resets_nothing(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    await run_next(jenv, fails_with("analysis_failed"))
    assert jenv.row(job_id).state == "failed"
    assert budget(jenv, job_id) == SPENT


async def test_operation_busy_keeps_what_was_spent(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    await run_next(jenv, fails_with("operation_busy"))
    assert jenv.row(job_id).state == "queued"
    assert budget(jenv, job_id) == SPENT


async def test_operation_busy_past_the_requeue_limit_keeps_what_was_spent(jenv: JobsEnv) -> None:
    """The one path where ``retry`` sees ``operation_busy`` (a counted attempt): no reset."""
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    lease = (await issue(jenv.factory, (await claim(jenv)).claimed)).lease
    async with fenced_session(jenv.factory, lease) as fs:
        assert await transitions.operation_busy(fs, 0.0, 0) == "queued"
    assert budget(jenv, job_id) == SPENT


async def test_pool_not_synced_keeps_what_was_spent(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    await run_next(jenv, fails_with("pool_not_synced"))
    assert jenv.row(job_id).state == "queued"
    assert budget(jenv, job_id) == SPENT


async def test_a_lease_lost_requeue_keeps_what_was_spent(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    await claim(jenv, "worker-a")
    jenv.expire_lease(job_id)
    assert await queue.reap(jenv.factory) == 1
    assert jenv.row(job_id).state == "queued"  # requeued by the reaper
    assert budget(jenv, job_id) == SPENT


async def test_a_hand_back_keeps_what_was_spent(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    lease = (await issue(jenv.factory, (await claim(jenv)).claimed)).lease
    async with fenced_session(jenv.factory, lease) as fs:
        assert await transitions.hand_back(fs) == "queued"
    assert budget(jenv, job_id) == SPENT


async def test_only_the_jobs_own_row_is_reset(jenv: JobsEnv) -> None:
    owner, job_id = jenv.job(max_attempts=3)
    other_owner, other_job = jenv.job(max_attempts=3)
    plant(jenv, owner, job_id)
    plant(jenv, other_owner, other_job)
    ran = (await run_next(jenv, fails_with("analysis_unavailable"))).claimed.job_id
    untouched = other_job if ran == job_id else job_id
    assert budget(jenv, ran) == (0.0, False)
    assert budget(jenv, untouched) == SPENT


async def test_a_job_with_no_budget_row_is_untouched(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(max_attempts=3)
    await run_next(jenv, fails_with("analysis_unavailable"))
    assert budget(jenv, job_id) is None  # nothing is created by a reset
