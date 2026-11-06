"""ME5 (#656, ADR 0022 section 4): the "class ready" mail via the worker's hand-off row.

The worker side (``classbeat.jobs.mail_handoff``) through the real runner and fence, with a test
kind; the web side (``classbeat.emails.handoff``) and ``mail_handoff_take`` with the SMTP fake.
The real producers (the design kinds and ``swedebeat_verify_mix`` on a fresh or cached mix) are
covered in ``test_swedebeat_design.py`` and ``test_swedebeat_verify.py``. Fakes only.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from classbeat.emails import REGISTRY, Category
from classbeat.emails import handoff as consumer
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun, LeaseLost, queue
from classbeat.jobs import mail_handoff as producer
from classbeat.jobs.fence import fenced_session
from classbeat.jobs.issuer import issue
from classbeat.mail import Mailer
from jobcore import JobsEnv, claim, registry_for, run_next
from jobtools import engine_for, new_class, new_job
from smtpfake import SmtpFake

pytestmark = pytest.mark.usefixtures("jenv")


def rows(env: JobsEnv, owner: UUID | None = None) -> list[Any]:
    where = "" if owner is None else " WHERE owner_id = :o"
    return env.sql(f"SELECT * FROM mail_handoff{where} ORDER BY created_at", o=owner).all()  # noqa: S608


def fresh(env: JobsEnv, job_id: UUID) -> bool:
    return env.sql("SELECT fresh_analysis FROM jobs WHERE id = :j", j=job_id).scalar_one()


def runs_on(env: JobsEnv) -> None:
    """Every queued test job may be claimed now (a retry's backoff skipped)."""
    env.sql("UPDATE jobs SET available_at = now() WHERE state = 'queued'")


async def mark(run: JobRun) -> None:
    async with run.fenced() as fs:
        await producer.note_fresh_analysis(fs)


async def finish(fs: FencedSession) -> None:
    await producer.enqueue_class_ready(fs)


def disable_kind(env: JobsEnv) -> None:
    env.sql(
        "INSERT INTO app_settings (key, value) VALUES ('mail_kinds_disabled', "
        "CAST(:v AS jsonb)) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        v=json.dumps(["class_ready"]),
    )


@pytest.fixture(autouse=True)
def no_settings(jobs_admin: Any) -> Any:
    yield
    jobs_admin.execute(text("DELETE FROM app_settings WHERE key = 'mail_kinds_disabled'"))


# --- the registry and the worker constant agree -------------------------------------------------


def test_the_worker_kind_is_the_registry_kind_and_the_admin_may_switch_it_off() -> None:
    kind = REGISTRY[producer.KIND]
    assert kind.category is Category.CLASS_READY
    assert kind.category.value == producer.KIND  # the user's preference is the category
    assert (kind.essential, kind.user_can_opt_out, kind.admin_can_disable) == (False, True, True)


# --- the worker: one row per job that ran a fresh analysis, in the finishing transaction ----------


async def test_a_job_that_ran_a_fresh_analysis_hands_off_one_row_with_its_class_only(
    jenv: JobsEnv,
) -> None:
    owner, job_id = jenv.job()

    async def run(job_run: JobRun) -> Complete:
        await mark(job_run)
        return Complete(finish)

    await run_next(jenv, registry_for(run))
    assert jenv.row(job_id).state == "completed"
    [row] = rows(jenv)
    class_id = jenv.row(job_id).class_id
    assert (row.owner_id, row.kind, row.job_id) == (owner, "class_ready", job_id)
    assert row.params == {"class_id": str(class_id)}  # the class id only: no address, no title


async def test_a_cached_mix_hands_off_nothing(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()
    await run_next(jenv, registry_for(lambda job_run: _complete()))
    assert jenv.row(job_id).state == "completed"
    assert not fresh(jenv, job_id)
    assert rows(jenv) == []


async def _complete() -> Complete:
    return Complete(finish)


async def test_a_retried_attempt_commits_no_row_and_the_job_ends_with_one(jenv: JobsEnv) -> None:
    """Attempt 1 analyses (the flag commits with the analysis) and then fails: it ends in a retry
    and commits no row. Attempt 2 finds the analysis cached and finishes: one row, the job's."""
    _, job_id = jenv.job()
    attempts: list[int] = []

    async def run(job_run: JobRun) -> Complete:
        attempts.append(job_run.attempt)
        if job_run.attempt == 1:
            await mark(job_run)
            raise JobFailure("upstream_unavailable")
        return Complete(finish)

    registry = registry_for(run)
    await run_next(jenv, registry)
    assert jenv.row(job_id).state == "queued"  # a retry
    assert fresh(jenv, job_id)
    assert rows(jenv) == []
    runs_on(jenv)
    await run_next(jenv, registry)
    assert attempts == [1, 2]
    assert jenv.row(job_id).state == "completed"
    assert [r.job_id for r in rows(jenv)] == [job_id]


async def test_a_stale_attempt_adds_nothing(jenv: JobsEnv) -> None:
    """The lease is lost after the analysis: the stale attempt's finish is refused by the fence and
    writes no row; the attempt that holds the lease adds the job's one row."""
    _, job_id = jenv.job()

    async def run(job_run: JobRun) -> Complete:
        if job_run.attempt == 1:
            await mark(job_run)
            jenv.expire_lease(job_id)
            assert await queue.reap(jenv.factory) == 1  # the token goes up
        return Complete(finish)

    registry = registry_for(run)
    execution = await run_next(jenv, registry)
    assert execution.result == "lost"
    assert rows(jenv) == []
    runs_on(jenv)
    await run_next(jenv, registry)
    assert [r.job_id for r in rows(jenv)] == [job_id]


async def test_the_insert_is_fenced_and_one_per_job(jenv: JobsEnv) -> None:
    _, job_id = jenv.job()
    execution = await claim(jenv)
    lease = (await issue(jenv.factory, execution.claimed)).lease
    async with fenced_session(jenv.factory, lease) as fs:
        await producer.note_fresh_analysis(fs)
        assert await producer.enqueue_class_ready(fs) is True
        assert await producer.enqueue_class_ready(fs) is False  # ON CONFLICT DO NOTHING
    assert len(rows(jenv)) == 1
    jenv.expire_lease(job_id)
    await queue.reap(jenv.factory)
    with pytest.raises(LeaseLost):
        async with fenced_session(jenv.factory, lease) as fs:
            await producer.enqueue_class_ready(fs)


@pytest.mark.parametrize("off", ["admin", "user_category", "user_all"])
async def test_the_worker_skips_the_row_when_the_mail_is_off(jenv: JobsEnv, off: str) -> None:
    owner, job_id = jenv.job()
    if off == "admin":
        disable_kind(jenv)
    elif off == "user_category":
        jenv.sql("UPDATE users SET mail_opted_out = ARRAY['class_ready'] WHERE id = :o", o=owner)
    else:
        jenv.sql("UPDATE users SET mail_unsubscribed_all = true WHERE id = :o", o=owner)

    async def run(job_run: JobRun) -> Complete:
        await mark(job_run)
        return Complete(finish)

    await run_next(jenv, registry_for(run))
    assert jenv.row(job_id).state == "completed"
    assert rows(jenv) == []


async def test_another_category_switched_off_does_not_stop_it(jenv: JobsEnv) -> None:
    owner, _ = jenv.job()
    jenv.sql("UPDATE users SET mail_opted_out = ARRAY['welcome'] WHERE id = :o", o=owner)

    async def run(job_run: JobRun) -> Complete:
        await mark(job_run)
        return Complete(finish)

    await run_next(jenv, registry_for(run))
    assert len(rows(jenv)) == 1


# --- the web process: take, check, send once ------------------------------------------------------


@pytest.fixture
async def smtp_fake() -> AsyncIterator[SmtpFake]:
    async with SmtpFake() as fake:
        yield fake


def mailer_for(env: JobsEnv, fake: SmtpFake) -> tuple[Mailer, Any]:
    settings = env.settings.model_copy(
        update={"smtp_host": "127.0.0.1", "smtp_port": fake.port, "smtp_tls": "none"}
    )
    return Mailer(settings), settings


def plant(env: JobsEnv, owner: UUID | None = None, *, age: str = "0 seconds", **params: Any):
    """A hand-off row as the worker writes it (setup as the superuser)."""
    owner = owner or env.user()
    class_id = new_class(env.admin, owner)
    job_id = new_job(env.admin, owner, class_id)
    env.sql(
        "INSERT INTO mail_handoff (owner_id, kind, job_id, params, created_at) VALUES "
        "(:o, 'class_ready', :j, CAST(:p AS jsonb), now() - CAST(:age AS interval))",
        o=owner,
        j=job_id,
        p=json.dumps({"class_id": str(class_id), **params}),
        age=age,
    )
    return owner, class_id


def email_of(env: JobsEnv, owner: UUID) -> str:
    return env.sql("SELECT email FROM users WHERE id = :o", o=owner).scalar_one()


async def test_a_taken_row_sends_one_mail_and_is_gone(jenv: JobsEnv, smtp_fake: SmtpFake) -> None:
    owner, class_id = plant(jenv)
    mailer, settings = mailer_for(jenv, smtp_fake)
    assert await consumer.drain(jenv.factory, mailer, settings) == 1
    assert rows(jenv) == []
    [mail] = smtp_fake.messages
    assert mail.to == email_of(jenv, owner)
    assert mail.subject == "Your classbeat class is ready"
    link = f"{settings.public_base_url}/classes/{class_id}"
    assert link in mail.text
    assert link in mail.html
    assert mail.message["List-Unsubscribe"]  # an optional kind carries the signed links
    assert await consumer.drain(jenv.factory, mailer, settings) == 0  # taken once only
    assert len(smtp_fake.messages) == 1


@pytest.mark.parametrize("off", ["admin", "user_category", "user_all", "disabled_account"])
async def test_the_web_side_checks_the_switch_and_the_preference_again(
    jenv: JobsEnv, smtp_fake: SmtpFake, off: str
) -> None:
    owner, _ = plant(jenv)  # written while the mail was on
    if off == "admin":
        disable_kind(jenv)
    elif off == "user_category":
        jenv.sql("UPDATE users SET mail_opted_out = ARRAY['class_ready'] WHERE id = :o", o=owner)
    elif off == "user_all":
        jenv.sql("UPDATE users SET mail_unsubscribed_all = true WHERE id = :o", o=owner)
    else:
        jenv.sql("UPDATE users SET disabled_at = now() WHERE id = :o", o=owner)
    mailer, settings = mailer_for(jenv, smtp_fake)
    assert await consumer.drain(jenv.factory, mailer, settings) == 0
    assert smtp_fake.messages == []
    assert rows(jenv) == []  # taken and discarded


async def test_a_deleted_class_sends_nothing(jenv: JobsEnv, smtp_fake: SmtpFake) -> None:
    owner, class_id = plant(jenv)
    jenv.sql("DELETE FROM classes WHERE id = :c", c=class_id)
    assert rows(jenv) == []  # the row goes with its job's class
    # A row taken before the delete committed: the class is checked again just before sending.
    other_owner, other_class = plant(jenv)
    jenv.sql(
        "UPDATE mail_handoff SET params = CAST(:p AS jsonb) WHERE owner_id = :o",
        p=json.dumps({"class_id": str(uuid.uuid4())}),
        o=other_owner,
    )
    # ... and a class id of another user's class is never the owner's.
    plant(jenv, owner)
    jenv.sql(
        "UPDATE mail_handoff SET params = CAST(:p AS jsonb) WHERE owner_id = :o",
        p=json.dumps({"class_id": str(other_class)}),
        o=owner,
    )
    mailer, settings = mailer_for(jenv, smtp_fake)
    assert await consumer.drain(jenv.factory, mailer, settings) == 0
    assert smtp_fake.messages == []


async def test_a_deleted_account_takes_its_rows_with_it(jenv: JobsEnv) -> None:
    owner, _ = plant(jenv)
    jenv.sql("DELETE FROM users WHERE id = :o", o=owner)
    assert rows(jenv) == []


async def test_rows_older_than_a_day_are_dropped_not_sent(
    jenv: JobsEnv, smtp_fake: SmtpFake
) -> None:
    plant(jenv, age="25 hours")
    owner, _ = plant(jenv)
    mailer, settings = mailer_for(jenv, smtp_fake)
    assert await consumer.drain(jenv.factory, mailer, settings) == 1
    assert [m.to for m in smtp_fake.messages] == [email_of(jenv, owner)]
    assert rows(jenv) == []


async def test_without_smtp_the_hourly_purge_drops_old_rows_and_keeps_new_ones(
    jenv: JobsEnv,
) -> None:
    plant(jenv, age="2 days")
    owner, _ = plant(jenv)
    unconfigured = Mailer(jenv.settings)
    assert not unconfigured.configured
    worker = consumer.HandoffConsumer(jenv.engine, jenv.factory, unconfigured, jenv.settings)
    worker.start()
    try:
        for _ in range(100):
            if len(rows(jenv)) == 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()
    [kept] = rows(jenv)
    assert kept.owner_id == owner  # purge only: a fresh row is never taken without SMTP


async def test_a_running_consumer_is_woken_by_the_notification(
    jenv: JobsEnv, smtp_fake: SmtpFake
) -> None:
    mailer, settings = mailer_for(jenv, smtp_fake)
    # A poll far away: only LISTEN mail_handoff can wake it in time.
    worker = consumer.HandoffConsumer(
        jenv.engine, jenv.factory, mailer, settings, poll_seconds=3600
    )
    worker.start()
    try:
        await asyncio.sleep(0.5)  # the first drain ran and the listener is up
        owner, _ = jenv.job()

        async def run(job_run: JobRun) -> Complete:
            await mark(job_run)
            return Complete(finish)

        await run_next(jenv, registry_for(run))
        for _ in range(100):
            if smtp_fake.messages:
                break
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()
    assert [m.to for m in smtp_fake.messages] == [email_of(jenv, owner)]


async def test_two_web_consumers_never_send_the_same_row(
    jenv: JobsEnv, smtp_fake: SmtpFake
) -> None:
    owner = jenv.user()
    for _ in range(45):
        plant(jenv, owner)
    mailer, settings = mailer_for(jenv, smtp_fake)
    sent = await asyncio.gather(
        consumer.drain(jenv.factory, mailer, settings),
        consumer.drain(jenv.factory, mailer, settings),
    )
    assert sum(sent) == 45
    assert len(smtp_fake.messages) == 45
    links = [m.text.split("/classes/")[1].split()[0] for m in smtp_fake.messages]
    assert len(set(links)) == 45  # each class's mail once
    assert rows(jenv) == []


def test_a_locked_row_is_skipped_by_another_take(jenv: JobsEnv, app_database_url: str) -> None:
    """Two transactions as the app role: the second take skips the row the first holds (and, once
    the first commits, the row is gone for good)."""
    plant(jenv)
    plant(jenv)
    take = text("SELECT id FROM public.mail_handoff_take(1)")
    with engine_for(app_database_url) as engine, engine.connect() as one, engine.connect() as two:
        first = one.execute(take).scalars().all()
        second = two.execute(take).scalars().all()
        assert len(first) == len(second) == 1
        assert first != second
        assert two.execute(take).scalars().all() == []  # the first's row is locked: skipped
        one.commit()
        two.commit()
    assert rows(jenv) == []


@pytest.mark.parametrize("limit", [-1, 101])
def test_the_take_refuses_a_limit_out_of_range(
    jenv: JobsEnv, app_database_url: str, limit: int
) -> None:
    with (
        engine_for(app_database_url) as engine,
        engine.connect() as connection,
        pytest.raises(DBAPIError),
    ):
        connection.execute(text("SELECT * FROM public.mail_handoff_take(:n)"), {"n": limit})


def test_the_app_role_cannot_read_or_delete_another_users_rows(
    jenv: JobsEnv, app_database_url: str
) -> None:
    """Row security is forced: without the take function, a session sees only its own rows."""
    owner, _ = plant(jenv)
    stranger = jenv.user()
    with engine_for(app_database_url) as engine, engine.begin() as connection:
        connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(stranger)})
        assert connection.execute(text("SELECT count(*) FROM mail_handoff")).scalar_one() == 0
        connection.execute(text("DELETE FROM mail_handoff"))
    assert [r.owner_id for r in rows(jenv)] == [owner]


async def test_a_failing_send_does_not_stop_the_other_rows(
    jenv: JobsEnv, smtp_fake: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    plant(jenv)
    plant(jenv)
    real: Callable[..., Any] = consumer.send_taken
    calls: list[int] = []

    async def flaky(*args: Any) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return await real(*args)

    monkeypatch.setattr(consumer, "send_taken", flaky)
    mailer, settings = mailer_for(jenv, smtp_fake)
    assert await consumer.drain(jenv.factory, mailer, settings) == 1
    assert len(smtp_fake.messages) == 1
    assert rows(jenv) == []  # fire and forget: the failed row is not retried


async def test_a_stop_ends_a_drain_that_has_full_batches_waiting(
    jenv: JobsEnv, smtp_fake: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _ = plant(jenv)
    for _ in range(consumer.BATCH):  # one more than a batch in all
        plant(jenv, owner)
    stop = asyncio.Event()

    async def send_and_stop(*args: Any) -> bool:
        stop.set()  # the stop arrives while the first batch is being sent
        return True

    monkeypatch.setattr(consumer, "send_taken", send_and_stop)
    mailer, settings = mailer_for(jenv, smtp_fake)
    assert await consumer.drain(jenv.factory, mailer, settings, stop) == consumer.BATCH
    assert len(rows(jenv)) == 1  # the second batch was left for the next start


async def test_a_cancelled_drain_logs_how_many_taken_rows_were_lost(
    jenv: JobsEnv,
    smtp_fake: SmtpFake,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner, _ = plant(jenv)
    plant(jenv, owner)
    plant(jenv, owner)
    entered = asyncio.Event()

    async def hang(*args: Any) -> bool:
        entered.set()
        await asyncio.Event().wait()
        return False

    monkeypatch.setattr(consumer, "send_taken", hang)
    mailer, settings = mailer_for(jenv, smtp_fake)
    task = asyncio.create_task(consumer.drain(jenv.factory, mailer, settings))
    await asyncio.wait_for(entered.wait(), timeout=10)
    with caplog.at_level("WARNING", logger=consumer.logger.name):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    [record] = [r for r in caplog.records if "lost at shutdown" in r.getMessage()]
    assert record.__dict__["count"] == 3  # the one being sent and the two waiting
    assert rows(jenv) == []
