"""The discard of a failed first creation's class (ADR 0039, ``classbeat.jobs.discard``) against the
real schema, through the worker's fenced ending, the settle step and the discard itself.

- A failed or stopped first reading, and the reading's failed first design, discard the class: the
  job is detached (no class, the old id kept, no input, a fresh key), the reading and the messages
  go with the class, the events stay.
- What keeps a class: a version, a song list, a saved workout, an uncertain write, a second design
  attempt, a design that is not the reading's own, another job, another kind, a retry.
- A replay of the discard and a stale lease change nothing; another user's class is never touched.
- The credit: the detached design keeps its reserved credit, which the settle step releases once;
  ``agent_runs`` rows survive.
"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

from classbeat.db import user_session
from classbeat.design.interpretation_row import handoff_key
from classbeat.jobs import (
    JobFailure,
    JobKind,
    JobRun,
    KindRegistry,
    LeaseLost,
    Outcome,
    fenced_session,
    queue,
    settle_one,
    transitions,
)
from classbeat.jobs.discard import discard_first_creation
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.quota import Bounds, MeteredLedger
from ctx import make_context
from jobcore import JobsEnv, KindInput, content_hash
from jobtools import digest, new_class, new_job
from test_plans_schema import ledger, row

pytestmark = pytest.mark.usefixtures("jenv")

READING = "interpret_request"


def _reading(env: JobsEnv, owner: UUID | None = None, **columns: Any) -> tuple[UUID, UUID, UUID]:
    """(owner, class, reading job): a start-screen class and its queued reading (no credit)."""
    owner = owner or env.user()
    class_id = new_class(env.admin, owner)
    values: dict[str, Any] = {"kind": READING, "credit_state": "none", **columns}
    return owner, class_id, new_job(env.admin, owner, class_id, **values)


def _read(env: JobsEnv, job_id: UUID) -> None:
    env.sql("UPDATE jobs SET state = 'completed', finished_at = now() WHERE id = :j", j=job_id)


def _design(
    env: JobsEnv, owner: UUID, class_id: UUID, reading: UUID, kind: str = "design_draft"
) -> UUID:
    """The reading's hand-off design, queued with a reserved credit."""
    return new_job(env.admin, owner, class_id, kind=kind, idempotency_key=handoff_key(reading))


def _fails(category: str) -> Callable[[JobRun], Any]:
    async def run(job_run: JobRun) -> Outcome:
        raise JobFailure(category)

    return run


async def _run(env: JobsEnv, kind: str, run: Callable[[JobRun], Any] | None = None) -> Execution:
    claimed = await queue.claim(env.factory, "discard-w", 60, [kind])
    assert claimed is not None, "nothing to claim"
    registry = KindRegistry()
    registry.register(JobKind(kind, KindInput, ("read",), run or _fails("model_refused")))
    execution = Execution(claimed)
    await execute(execution, env.factory, registry, RunConfig(lease_seconds=60), asyncio.Event())
    return execution


def _exists(env: JobsEnv, class_id: UUID) -> bool:
    return env.sql("SELECT 1 FROM classes WHERE id = :c", c=class_id).first() is not None


def _message(env: JobsEnv, owner: UUID, class_id: UUID, job_id: UUID) -> None:
    env.sql(
        "INSERT INTO class_messages (owner_id, class_id, seq, role, body, job_id) "
        "VALUES (:o, :c, 1, 'user', 'A 30 minute class', :j)",
        o=owner,
        c=class_id,
        j=job_id,
    )


def _revision(env: JobsEnv, owner: UUID, class_id: UUID, kind: str = "draft") -> UUID:
    revision_id = uuid.uuid4()
    env.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 1, :k, '{}'::jsonb, :h, "
        "'{}'::jsonb, '{}'::jsonb)",
        id=revision_id,
        o=owner,
        c=class_id,
        k=kind,
        h=content_hash({}),
    )
    return revision_id


async def test_a_failed_first_reading_discards_its_class(jenv: JobsEnv) -> None:
    owner, class_id, job_id = _reading(jenv)
    _message(jenv, owner, class_id, job_id)
    before = jenv.row(job_id).idempotency_key
    await _run(jenv, READING)
    assert not _exists(jenv, class_id)
    job = jenv.row(job_id)
    assert (job.state, job.error_category) == ("failed", "model_refused")
    assert (job.class_id, job.discarded_class_id, job.input) == (None, class_id, {})
    assert job.idempotency_key != before
    assert job.idempotency_key.startswith("discarded-")
    assert jenv.kinds(job_id)[-1] == "failed"  # the events stay with the job
    left = jenv.sql("SELECT count(*) FROM class_messages WHERE class_id = :c", c=class_id)
    assert left.scalar_one() == 0


async def test_a_stopped_first_reading_is_discarded_by_the_worker(jenv: JobsEnv) -> None:
    _, class_id, job_id = _reading(jenv)

    async def stopped(job_run: JobRun) -> Outcome:
        jenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :j", j=job_id)
        await job_run.checkpoint()
        raise AssertionError("not reached")

    await _run(jenv, READING, stopped)
    assert jenv.row(job_id).state == "cancelled"
    assert not _exists(jenv, class_id)


@pytest.mark.parametrize("kind", ["design_draft", "design_with_songs"])
async def test_the_readings_failed_first_design_discards_the_class(
    jenv: JobsEnv, kind: str
) -> None:
    owner, class_id, reading = _reading(jenv)
    _read(jenv, reading)
    design = _design(jenv, owner, class_id, reading, kind)
    await _run(jenv, kind)
    assert not _exists(jenv, class_id)
    assert jenv.sql("SELECT 1 FROM jobs WHERE id = :j", j=reading).first() is None
    job = jenv.row(design)
    assert (job.state, job.class_id, job.discarded_class_id) == ("failed", None, class_id)


def _with_version(env: JobsEnv, owner: UUID, class_id: UUID) -> None:
    revision_id = _revision(env, owner, class_id)
    env.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=revision_id, c=class_id)


def _with_song_list(env: JobsEnv, owner: UUID, class_id: UUID) -> None:
    _revision(env, owner, class_id, "song_list")


def _with_saved_workout(env: JobsEnv, owner: UUID, class_id: UUID) -> None:
    env.sql(
        "INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, "
        "content_hash, name) VALUES (:o, :c, 4242, 1, :h, 'Tuesday climb')",
        o=owner,
        c=class_id,
        h=bytes(32),
    )


def _with_uncertain_write(env: JobsEnv, owner: UUID, class_id: UUID) -> None:
    env.sql(
        "INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
        "lease_version, class_id) VALUES (:o, 'mowl_save_workout', :k, :h, 'uncertain', 1, :c)",
        o=owner,
        c=class_id,
        k=uuid.uuid4().hex,
        h=digest("op"),
    )


def _with_a_finished_job(env: JobsEnv, owner: UUID, class_id: UUID) -> None:
    new_job(env.admin, owner, class_id, state="completed", finished_at="2026-01-01")


KEEPS = [_with_version, _with_song_list, _with_saved_workout, _with_uncertain_write]


def _with_a_stored_reading(env: JobsEnv, owner: UUID, class_id: UUID) -> None:
    env.sql("UPDATE classes SET interpretation = '{}'::jsonb WHERE id = :c", c=class_id)


@pytest.mark.parametrize("keep", [*KEEPS, _with_a_finished_job, _with_a_stored_reading])
async def test_a_reading_whose_class_holds_anything_keeps_it(
    jenv: JobsEnv, keep: Callable[[JobsEnv, UUID, UUID], None]
) -> None:
    owner, class_id, job_id = _reading(jenv)
    keep(jenv, owner, class_id)
    await _run(jenv, READING)
    assert _exists(jenv, class_id)
    assert (jenv.row(job_id).class_id, jenv.row(job_id).discarded_class_id) == (class_id, None)


@pytest.mark.parametrize("keep", KEEPS)
async def test_a_first_design_whose_class_holds_anything_keeps_it(
    jenv: JobsEnv, keep: Callable[[JobsEnv, UUID, UUID], None]
) -> None:
    owner, class_id, reading = _reading(jenv)
    _read(jenv, reading)
    design = _design(jenv, owner, class_id, reading)
    keep(jenv, owner, class_id)
    await _run(jenv, "design_draft")
    assert _exists(jenv, class_id)
    assert jenv.row(design).class_id == class_id


def _operation(env: JobsEnv, owner: UUID, job_id: UUID, state: str) -> None:
    """An owner-level write of a job (the music stage's MOWL attach: no class of its own)."""
    env.sql(
        "INSERT INTO external_operations (owner_id, job_id, kind, idempotency_key, request_hash, "
        "state, external_id, lease_version) "
        "VALUES (:o, :j, 'mowl_attach_spotify_playlist', :k, :h, :s, 'pl-1', 1)",
        o=owner,
        j=job_id,
        k=uuid.uuid4().hex,
        h=digest("attach"),
        s=state,
    )


@pytest.mark.parametrize(
    ("whose", "state", "discarded"),
    [
        ("design", "succeeded", True),  # finished, and it stays with the detached design
        ("design", "failed", True),
        ("design", "pending", False),  # its outcome unknown: the class stays
        ("design", "uncertain", False),
        ("reading", "succeeded", False),  # another job's write would go with the class
    ],
)
async def test_a_jobs_own_finished_write_does_not_keep_the_class(
    jenv: JobsEnv, whose: str, state: str, discarded: bool
) -> None:
    owner, class_id, reading = _reading(jenv)
    _read(jenv, reading)
    design = _design(jenv, owner, class_id, reading)
    _operation(jenv, owner, design if whose == "design" else reading, state)
    await _run(jenv, "design_draft")
    assert _exists(jenv, class_id) is not discarded
    linked = jenv.sql("SELECT count(*) FROM external_operations WHERE job_id = :j", j=design)
    assert linked.scalar_one() == (1 if whose == "design" else 0)


async def test_a_second_design_attempt_keeps_the_class(jenv: JobsEnv) -> None:
    owner, class_id, reading = _reading(jenv)
    _read(jenv, reading)
    first = _design(jenv, owner, class_id, reading)
    jenv.sql(
        "UPDATE jobs SET state = 'failed', error_category = 'internal', finished_at = now() "
        "WHERE id = :j",
        j=first,
    )  # an earlier attempt the discard did not take (before ADR 0039, or the reaper's)
    second = new_job(jenv.admin, owner, class_id, kind="design_draft")
    await _run(jenv, "design_draft")
    assert jenv.row(second).state == "failed"
    assert _exists(jenv, class_id)


async def test_a_design_that_is_not_the_readings_hand_off_keeps_the_class(jenv: JobsEnv) -> None:
    owner, class_id, reading = _reading(jenv)
    _read(jenv, reading)
    new_job(jenv.admin, owner, class_id, kind="design_draft")  # another key: not the hand-off's
    await _run(jenv, "design_draft")
    assert _exists(jenv, class_id)


async def test_a_design_with_no_reading_keeps_its_class(jenv: JobsEnv) -> None:
    """The class form's design (a class made by title) is not a start-screen creation."""
    owner = jenv.user()
    class_id = new_class(jenv.admin, owner)
    new_job(jenv.admin, owner, class_id, kind="design_draft")
    await _run(jenv, "design_draft")
    assert _exists(jenv, class_id)


async def test_another_kind_and_a_retry_keep_the_class(jenv: JobsEnv) -> None:
    _, class_id, job_id = _reading(jenv, kind="test_kind")
    await _run(jenv, "test_kind")
    assert (jenv.row(job_id).state, _exists(jenv, class_id)) == ("failed", True)
    _, class_id, job_id = _reading(jenv)
    await _run(jenv, READING, _fails("upstream_unavailable"))  # retryable: queued again
    assert (jenv.row(job_id).state, _exists(jenv, class_id)) == ("queued", True)


async def test_a_replay_and_a_stale_lease_change_nothing(jenv: JobsEnv) -> None:
    owner, class_id, job_id = _reading(jenv)
    execution = await _run(jenv, READING)
    detached = jenv.row(job_id)
    ctx = make_context(owner)
    async with user_session(jenv.factory, ctx) as db:
        assert await discard_first_creation(db, owner, job_id) is None
    assert execution.lease is not None
    with pytest.raises(LeaseLost):
        async with fenced_session(jenv.factory, execution.lease) as fs:
            await transitions.fail(fs, "internal")
    assert jenv.row(job_id) == detached
    assert not _exists(jenv, class_id)


async def test_another_users_ended_job_is_never_theirs_to_discard(jenv: JobsEnv) -> None:
    owner, class_id, job_id = _reading(jenv)
    jenv.sql(
        "UPDATE jobs SET state = 'failed', error_category = 'internal', finished_at = now() "
        "WHERE id = :j",
        j=job_id,
    )
    other = jenv.user()
    async with user_session(jenv.factory, make_context(other)) as db:
        assert await discard_first_creation(db, other, job_id) is None
    assert _exists(jenv, class_id)
    async with user_session(jenv.factory, make_context(owner)) as db:  # the owner's would
        assert await discard_first_creation(db, owner, job_id) == class_id


async def test_the_detached_design_keeps_its_credit_for_settle_and_its_usage(
    jenv: JobsEnv,
) -> None:
    owner, class_id, reading = _reading(jenv)
    _read(jenv, reading)
    design = _design(jenv, owner, class_id, reading)
    held = ledger(jenv.admin, owner, design)
    for job_id in (reading, design):
        jenv.sql(
            "INSERT INTO agent_runs (owner_id, job_id, job_kind, price_version, stage, attempt, "
            "provider, model, effort, prompt_version, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_micros, outcome, started_at, "
            "finished_at) VALUES (:o, :j, 'design_draft', 'v', 'design', 1, 'fake', 'm', "
            "'medium', 'x', 1, 1, 0, 0, 7, 'submitted', now(), now())",
            o=owner,
            j=job_id,
        )
    await _run(jenv, "design_draft")
    assert not _exists(jenv, class_id)
    assert (jenv.row(design).credit_state, row(jenv.admin, held).state) == ("reserved", "reserved")
    metered = MeteredLedger(Bounds())
    assert await settle_one(jenv.factory, metered, owner, design) == "released"
    assert await settle_one(jenv.factory, metered, owner, design) is None  # exactly once
    assert (row(jenv.admin, held).state, row(jenv.admin, held).job_id) == ("released", design)
    runs = jenv.sql(
        "SELECT job_id FROM agent_runs WHERE owner_id = :o ORDER BY job_id NULLS FIRST", o=owner
    ).scalars()
    assert list(runs) == [None, design]  # the reading's run outlives it, unlinked


def test_the_hand_off_key_is_the_one_the_discard_matches() -> None:
    reading = uuid.uuid4()
    assert handoff_key(reading) == "interpret-" + str(reading).replace("-", "")
