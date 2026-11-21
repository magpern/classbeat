"""A job failed ``analysis_unavailable`` is retried with backoff, then fails with it (ADR 0040
amendment item 4, #1352); ``analysis_failed`` is not retried. Needs the test database."""

import uuid

import pytest

from classbeat.jobs import Complete, JobFailure, JobRun
from jobcore import JobsEnv, registry_for, run_next

pytestmark = pytest.mark.usefixtures("jenv")


def ready(env: JobsEnv, job_id: uuid.UUID) -> None:
    env.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)


async def test_analysis_unavailable_is_retried_with_backoff_then_fails_with_its_category(
    jenv: JobsEnv,
) -> None:
    _, job_id = jenv.job(max_attempts=2)

    async def run(job_run: JobRun) -> Complete:
        raise JobFailure("analysis_unavailable")

    registry = registry_for(run)
    await run_next(jenv, registry)
    row = jenv.row(job_id)
    assert (row.state, row.error_category) == ("queued", None)
    wait = jenv.sql(
        "SELECT extract(epoch FROM available_at - now()) FROM jobs WHERE id = :id", id=job_id
    ).scalar_one()
    assert 20 < wait <= 40  # ADR 0005 section 4: 30 s for the first retry, with jitter
    [*_, (_, kind, payload, _)] = jenv.events(job_id)
    assert (kind, payload["category"]) == ("retry_scheduled", "analysis_unavailable")
    ready(jenv, job_id)
    await run_next(jenv, registry)
    row = jenv.row(job_id)
    assert (row.state, row.error_category, row.attempt) == ("failed", "analysis_unavailable", 2)
    [*_, (_, kind, payload, _)] = jenv.events(job_id)
    assert (kind, payload) == ("failed", {"category": "analysis_unavailable"})


async def test_analysis_failed_is_not_retried(jenv: JobsEnv) -> None:
    _, job_id = jenv.job(max_attempts=3)

    async def run(job_run: JobRun) -> Complete:
        raise JobFailure("analysis_failed")

    await run_next(jenv, registry_for(run))
    row = jenv.row(job_id)
    assert (row.state, row.error_category, row.attempt) == ("failed", "analysis_failed", 1)
