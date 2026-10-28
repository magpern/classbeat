"""The queue's SECURITY DEFINER functions (ADR 0005 section 3), called as classbeat_app."""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from jobtools import digest, engine_for, events, job, new_class, new_job, new_user

CLAIM = "SELECT * FROM public.queue_claim(:w, :lease, CAST(:kinds AS text[]))"


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url, pool_size=20) as engine:
        yield engine


def call(app: Engine, sql: str, **params: Any) -> list[Any]:
    with app.begin() as connection:
        return list(connection.execute(text(sql), params).all())


def claim(
    app: Engine, worker: str = "w1", lease: int = 60, kinds: tuple[str, ...] = ("test_kind",)
):
    return call(app, CLAIM, w=worker, lease=lease, kinds=list(kinds))


def expire_lease(admin: Connection, job_id: uuid.UUID) -> None:
    admin.execute(
        text("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
        {"id": job_id},
    )


def test_a_claim_leases_the_job_and_writes_its_event(admin: Connection, app: Engine) -> None:
    owner = new_user(admin)
    job_id = new_job(admin, owner)
    [row] = claim(app, worker="host-1:42", lease=60)
    assert (row.job_id, row.owner_id, row.lease_version) == (job_id, owner, 1)
    assert set(row._fields) == {"job_id", "owner_id", "lease_version"}
    j = job(admin, job_id)
    assert (j.state, j.attempt, j.leased_by, j.next_seq) == ("running", 1, "host-1:42", 1)
    assert j.started_at is not None
    assert j.heartbeat_at is not None
    lease = admin.execute(
        text("SELECT extract(epoch FROM lease_expires_at - now()) FROM jobs WHERE id = :id"),
        {"id": job_id},
    ).scalar_one()
    assert 55 < lease <= 60
    assert events(admin, job_id) == [(1, "claimed", {"attempt": 1}, 1)]
    assert claim(app) == []


def test_the_app_role_sees_no_job_rows_but_the_functions_work(
    admin: Connection, app: Engine
) -> None:
    new_job(admin, new_user(admin))
    assert call(app, "SELECT id FROM jobs") == []  # RLS: no app.user_id, no rows
    assert len(claim(app)) == 1


def test_the_claim_skips_other_kinds_future_and_spent_rows(admin: Connection, app: Engine) -> None:
    owner = new_user(admin)
    new_job(admin, owner, kind="other_kind")
    future = new_job(admin, owner)
    admin.execute(
        text("UPDATE jobs SET available_at = now() + interval '1 hour' WHERE id = :id"),
        {"id": future},
    )
    new_job(admin, owner, attempt=3, max_attempts=3)
    assert claim(app) == []
    assert claim(app, kinds=()) == []
    assert len(claim(app, kinds=("other_kind",))) == 1


@pytest.mark.parametrize(
    ("worker", "lease"),
    [("has space", 60), ("", 60), ("x" * 101, 60), ("w1", 29), ("w1", 601)],
)
def test_the_claim_refuses_bad_arguments(
    app: Engine, clean_auth: None, worker: str, lease: int
) -> None:
    with pytest.raises(DBAPIError, match=r"invalid worker id|p_lease_seconds"):
        claim(app, worker=worker, lease=lease)


@pytest.mark.parametrize("function", ["queue_reap", "queue_expire", "queue_unsettled"])
@pytest.mark.parametrize("limit", [0, 101])
def test_the_limits_are_checked(app: Engine, clean_auth: None, function: str, limit: int) -> None:
    with pytest.raises(DBAPIError, match="p_limit must be 1 to 100"):
        call(app, f"SELECT * FROM public.{function}(:n)", n=limit)  # noqa: S608


def _race(n: int, target: Any) -> None:
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait()
            target()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


def test_concurrent_claims_give_one_job_to_exactly_one_claimer(
    admin: Connection, app: Engine
) -> None:
    job_id = new_job(admin, new_user(admin))
    results: list[list[Any]] = []
    _race(16, lambda: results.append(claim(app)))
    winners = [r for r in results if r]
    assert len(results) == 16
    assert len(winners) == 1
    assert winners[0][0].job_id == job_id
    assert [e[1] for e in events(admin, job_id)] == ["claimed"]


def test_n_jobs_and_m_claimers_claim_each_job_once(admin: Connection, app: Engine) -> None:
    users = [new_user(admin) for _ in range(4)]
    jobs = {new_job(admin, users[i % 4]) for i in range(24)}
    claimed: list[uuid.UUID] = []
    lock = threading.Lock()

    def drain() -> None:
        while rows := claim(app):
            with lock:
                claimed.append(rows[0].job_id)

    _race(8, drain)
    assert sorted(claimed) == sorted(jobs)
    for job_id in jobs:
        assert job(admin, job_id).attempt == 1
        assert [e[1] for e in events(admin, job_id)] == ["claimed"]


def test_a_poison_row_does_not_block_other_users_and_the_reaper_fails_it(
    admin: Connection, app: Engine
) -> None:
    poisoned_user, other_user = new_user(admin), new_user(admin)
    poison = new_job(admin, poisoned_user, attempt=3, max_attempts=3)
    admin.execute(
        text("UPDATE jobs SET available_at = now() - interval '1 day' WHERE id = :id"),
        {"id": poison},
    )
    good = new_job(admin, other_user)
    [row] = claim(app)
    assert (row.job_id, row.owner_id) == (good, other_user)
    assert call(app, "SELECT public.queue_reap(10)")[0][0] == 1
    p = job(admin, poison)
    assert (p.state, p.error_category, p.attempt) == ("failed", "internal", 3)
    assert p.finished_at is not None
    assert events(admin, poison) == [(1, "failed", {"category": "internal"}, 0)]
    assert job(admin, good).state == "running"


def test_the_reaper_requeues_fails_or_cancels_expired_leases(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    retry, spent, cancel, alive = (new_job(admin, owner) for _ in range(4))
    admin.execute(text("UPDATE jobs SET max_attempts = 1 WHERE id = :id"), {"id": spent})
    for _ in range(4):
        assert claim(app)
    admin.execute(
        text("UPDATE jobs SET cancel_requested_at = now() WHERE id = :id"), {"id": cancel}
    )
    for job_id in (retry, spent, cancel):
        expire_lease(admin, job_id)
    assert call(app, "SELECT public.queue_reap(100)")[0][0] == 3

    r = job(admin, retry)
    assert (r.state, r.attempt, r.lease_version, r.leased_by, r.lease_expires_at) == (
        "queued", 1, 2, None, None,
    )  # fmt: skip
    seq, kind, payload, version = events(admin, retry)[-1]
    assert (seq, kind, payload["category"], version) == (2, "retry_scheduled", "lease_lost", 2)
    delay = admin.execute(
        text("SELECT extract(epoch FROM available_at - now()) FROM jobs WHERE id = :id"),
        {"id": retry},
    ).scalar_one()
    assert 20 < delay <= 36  # 30 s for attempt 1, +-20 % jitter

    s = job(admin, spent)
    assert (s.state, s.error_category, s.lease_version) == ("failed", "lease_lost", 2)
    assert events(admin, spent)[-1][1:3] == ("failed", {"category": "lease_lost"})
    c = job(admin, cancel)
    assert (c.state, c.error_category, c.finished_at is not None) == ("cancelled", None, True)
    assert events(admin, cancel)[-1][1] == "cancelled"
    assert job(admin, alive).state == "running"


def test_a_reaped_job_is_claimed_again_under_a_new_token(admin: Connection, app: Engine) -> None:
    job_id = new_job(admin, new_user(admin))
    assert claim(app)[0].lease_version == 1
    expire_lease(admin, job_id)
    call(app, "SELECT public.queue_reap(10)")
    admin.execute(text("UPDATE jobs SET available_at = now() WHERE id = :id"), {"id": job_id})
    [row] = claim(app, worker="w2")
    assert row.lease_version == 3  # the reaper's bump fenced the first holder
    j = job(admin, job_id)
    assert (j.attempt, j.leased_by) == (2, "w2")


def test_expired_waits_expire_and_the_rest_stay(admin: Connection, app: Engine) -> None:
    owner = new_user(admin)
    waiting = {
        "state": "waiting_for_approval",
        "approval_hash": digest("list"),
        "expires_at": "2099-01-01T00:00:00Z",
    }
    old = new_job(admin, owner, **waiting)
    fresh = new_job(admin, owner, **waiting)
    admin.execute(
        text(
            "UPDATE jobs SET expires_at = CASE WHEN id = :old THEN now() - interval '1 minute' "
            "ELSE now() + interval '1 day' END WHERE id IN (:old, :fresh)"
        ),
        {"old": old, "fresh": fresh},
    )
    assert call(app, "SELECT public.queue_expire(10)")[0][0] == 1
    assert job(admin, old).state == "expired"
    assert job(admin, old).finished_at is not None
    assert events(admin, old) == [(1, "expired", {}, 0)]
    assert job(admin, fresh).state == "waiting_for_approval"


def test_unsettled_lists_ended_jobs_with_reserved_credit_only(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    done: dict[str, Any] = {"state": "completed", "finished_at": "2026-10-28T00:00:00Z"}
    reserved = new_job(admin, owner, **done)
    new_job(admin, owner, **done, credit_state="committed")
    new_job(admin, owner)  # queued, reserved: not ended
    rows = call(app, "SELECT * FROM public.queue_unsettled(10)")
    assert [(r.job_id, r.owner_id, r.lease_version) for r in rows] == [(reserved, owner, 0)]


def test_stats_count_per_state_and_uncertain_operations(admin: Connection, app: Engine) -> None:
    owner = new_user(admin)
    new_job(admin, owner)
    new_job(admin, owner, state="failed", error_category="internal", finished_at="2026-10-28")
    admin.execute(
        text(
            "INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, "
            "state, lease_version) VALUES (:o, 'mowl_attach_spotify_playlist', 'k', :h, "
            "'uncertain', 1)"
        ),
        {"o": owner, "h": digest("op")},
    )
    stats = dict(call(app, "SELECT metric, total FROM public.queue_stats()"))
    assert (stats["jobs_queued"], stats["jobs_failed"], stats["jobs_running"]) == (1, 1, 0)
    assert stats["external_operations_uncertain"] == 1
    assert len(stats) == 8


def test_the_one_open_job_per_class_rule_holds_for_claimed_jobs(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    new_job(admin, owner, class_id)
    claim(app)
    with pytest.raises(DBAPIError, match="jobs_one_open_per_class"):
        new_job(admin, owner, class_id)
