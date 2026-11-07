"""The queue's SECURITY DEFINER functions (ADR 0005 section 3), called as classbeat_app."""

import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from jobtools import digest, engine_for, events, job, new_class, new_job, new_user

CLAIM = "SELECT * FROM public.queue_claim(:w, :lease, CAST(:kinds AS text[]), :slots)"


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
    app: Engine,
    worker: str = "w1",
    lease: int = 60,
    kinds: tuple[str, ...] = ("test_kind",),
    slots: int = 1,
):
    return call(app, CLAIM, w=worker, lease=lease, kinds=list(kinds), slots=slots)


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
    [(seq, kind, payload, version)] = events(admin, job_id)
    assert (seq, kind, version) == (1, "claimed", 1)
    assert set(payload) == {"attempt", "analysis_slot", "wait_ms"}
    assert (payload["attempt"], payload["analysis_slot"]) == (1, False)
    assert 0 <= payload["wait_ms"] < 60_000
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


@pytest.mark.parametrize("slots", [0, 17, None])
def test_the_claim_refuses_a_bad_analysis_cap(
    admin: Connection, app: Engine, slots: int | None
) -> None:
    new_job(admin, new_user(admin))
    with pytest.raises(DBAPIError, match="p_analysis_slots must be 1 to 16"):
        call(app, CLAIM, w="w1", lease=60, kinds=["test_kind"], slots=slots)


def test_the_claim_refuses_a_snapshot_isolation(admin: Connection, app: Engine) -> None:
    """#574: the slot count must see every claim committed before the lock was granted."""
    job_id = new_job(admin, new_user(admin))
    for level in ("REPEATABLE READ", "SERIALIZABLE"):
        with app.connect() as connection:
            connection.execute(text(f"SET TRANSACTION ISOLATION LEVEL {level}"))
            with pytest.raises(DBAPIError, match="READ COMMITTED only"):
                connection.execute(
                    text(CLAIM), {"w": "w1", "lease": 60, "kinds": ["test_kind"], "slots": 1}
                )
    assert job(admin, job_id).state == "queued"


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
    assert len(stats) == 18
    assert (stats["claim_wait_slot_samples_7d"], stats["claim_wait_slot_p95_ms_7d"]) == (0, None)
    assert stats["claim_wait_slot_peak_hour_utc"] is None
    assert (stats["analysis_samples_7d"], stats["analysis_fetch_p95_ms_7d"]) == (0, None)


def claimed_wait(admin: Connection, job_id: uuid.UUID) -> list[tuple[int, bool]]:
    return [
        (p["wait_ms"], p["analysis_slot"]) for _, k, p, _ in events(admin, job_id) if k == "claimed"
    ]


def test_each_claim_records_its_wait_from_available_at_and_whether_it_holds_a_slot(
    admin: Connection, app: Engine
) -> None:
    """#735: the claimed event's wait runs from the job's ``available_at`` (set again by every
    requeue) to that claim, so a requeued job's second claim measures its own wait, while
    ``started_at`` keeps the first claim's time."""
    owner = new_user(admin)
    job_id = new_job(admin, owner)
    admin.execute(
        text("UPDATE jobs SET available_at = now() - interval '90 seconds' WHERE id = :id"),
        {"id": job_id},
    )
    claim(app)
    [(first, slot)] = claimed_wait(admin, job_id)
    assert 90_000 <= first < 150_000
    assert slot is False
    started = job(admin, job_id).started_at
    # As ``transitions.wait_for_analysis_slot`` requeues it: with the slot, available now.
    admin.execute(
        text(
            "UPDATE jobs SET state = 'queued', attempt = attempt - 1, analysis_slot = true, "
            "available_at = now() - interval '2 seconds', lease_version = lease_version + 1, "
            "leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL WHERE id = :id"
        ),
        {"id": job_id},
    )
    claim(app)
    [_, (second, slot)] = claimed_wait(admin, job_id)
    assert 2_000 <= second < 60_000
    assert slot is True
    assert job(admin, job_id).started_at == started


def add_claims(admin: Connection, owner: uuid.UUID, rows: list[tuple[str, dict[str, Any]]]) -> None:
    """``claimed`` events at ``created_at`` (SQL expressions) with these payloads, on one job."""
    job_id = new_job(admin, owner)
    for seq, (at, payload) in enumerate(rows, start=1):
        values = f"(:o, :j, :s, 'claimed', CAST(:p AS jsonb), 1, {at})"
        insert = "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version, "
        sql = insert + "created_at) VALUES " + values  # noqa: S608 (the test's own literals)
        admin.execute(text(sql), {"o": owner, "j": job_id, "s": seq, "p": json.dumps(payload)})


def test_stats_give_the_p95_waits_at_peak_and_the_analysis_times(
    admin: Connection, app: Engine
) -> None:
    """#735: p95 (``percentile_disc``) of the last 7 days, slot and other claims apart, NULL below
    20 samples; the peak is the UTC hour of the day with the most slot claims, pooled across the
    days; legacy events without the fields and events older than 7 days do not count."""
    owner = new_user(admin)
    day = "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' - interval '1 day'"
    evening = [  # 20 slot claims at 18:xx UTC yesterday, 1 s to 20 s
        (f"{day} + interval '18 hours' + interval '{i} minutes'",
         {"attempt": 1, "analysis_slot": True, "wait_ms": i * 1000})
        for i in range(1, 21)
    ]  # fmt: skip
    night = [  # 5 slot claims at 03:xx UTC two days ago, 500 s each
        (f"{day} - interval '1 day' + interval '3 hours' + interval '{i} minutes'",
         {"attempt": 1, "analysis_slot": True, "wait_ms": 500_000})
        for i in range(5)
    ]  # fmt: skip
    other = [("now()", {"attempt": 1, "analysis_slot": False, "wait_ms": 7}) for _ in range(19)]
    ignored = [
        ("now() - interval '8 days'", {"attempt": 1, "analysis_slot": True, "wait_ms": 9}),
        ("now()", {"attempt": 1}),  # written before classbeat-queue v5
    ]
    add_claims(admin, owner, evening + night + other + ignored)
    for i in range(1, 21):
        new_job(
            admin,
            owner,
            analysis_fetch_ms=i * 100,
            analysis_decode_ms=i * 1000,
            analysed_at="now()",
            state="completed",
            finished_at="now()",
        )
    new_job(
        admin,
        owner,
        analysis_fetch_ms=1,
        analysis_decode_ms=1,
        analysed_at="2026-01-01",
        state="completed",
        finished_at="2026-01-01",
    )
    stats = dict(call(app, "SELECT metric, total FROM public.queue_stats()"))
    assert stats["claim_wait_slot_samples_7d"] == 25
    assert stats["claim_wait_slot_p95_ms_7d"] == 500_000
    assert (stats["claim_wait_other_samples_7d"], stats["claim_wait_other_p95_ms_7d"]) == (19, None)
    assert stats["claim_wait_slot_peak_hour_utc"] == 18
    assert stats["claim_wait_slot_peak_samples"] == 20
    assert stats["claim_wait_slot_peak_p95_ms"] == 19_000
    assert stats["analysis_samples_7d"] == 20
    assert stats["analysis_fetch_p95_ms_7d"] == 1_900
    assert stats["analysis_decode_p95_ms_7d"] == 19_000
    add_claims(admin, owner, [("now()", {"attempt": 1, "analysis_slot": False, "wait_ms": 70})])
    stats = dict(call(app, "SELECT metric, total FROM public.queue_stats()"))
    assert (stats["claim_wait_other_samples_7d"], stats["claim_wait_other_p95_ms_7d"]) == (20, 7)


def test_stats_skip_a_wait_outside_zero_to_1e12_instead_of_failing(
    admin: Connection, app: Engine
) -> None:
    """A ``claimed`` event whose ``wait_ms`` is negative or above 1e12 (the model's bound, and
    past what the bigint cast takes) is left out of the measurement; the function still answers."""
    owner = new_user(admin)
    rows = [
        ("now()", {"attempt": 1, "analysis_slot": True, "wait_ms": 5}),
        ("now()", {"attempt": 1, "analysis_slot": True, "wait_ms": 10**12}),
        ("now()", {"attempt": 1, "analysis_slot": True, "wait_ms": 10**12 + 1}),
        ("now()", {"attempt": 1, "analysis_slot": True, "wait_ms": 10**30}),
        ("now()", {"attempt": 1, "analysis_slot": True, "wait_ms": -1}),
    ]
    add_claims(admin, owner, rows)
    stats = dict(call(app, "SELECT metric, total FROM public.queue_stats()"))
    assert stats["claim_wait_slot_samples_7d"] == 2


def test_stats_peak_hour_tie_goes_to_the_earlier_hour(admin: Connection, app: Engine) -> None:
    """Two UTC hours with the same number of slot claims: the earlier hour is the peak."""
    owner = new_user(admin)
    day = "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' - interval '1 day'"
    payload = {"attempt": 1, "analysis_slot": True, "wait_ms": 1}
    rows = [(f"{day} + interval '{h} hours' + interval '{i} minutes'", payload)
            for h in (20, 6) for i in range(3)]  # fmt: skip
    add_claims(admin, owner, rows)
    stats = dict(call(app, "SELECT metric, total FROM public.queue_stats()"))
    assert stats["claim_wait_slot_peak_hour_utc"] == 6
    assert stats["claim_wait_slot_peak_samples"] == 3


def test_the_one_open_job_per_class_rule_holds_for_claimed_jobs(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    new_job(admin, owner, class_id)
    claim(app)
    with pytest.raises(DBAPIError, match="jobs_one_open_per_class"):
        new_job(admin, owner, class_id)


# Issue #574: the separate cap on running jobs that analyse a mix (``jobs.analysis_slot``).


def slot_job(admin: Connection, owner: uuid.UUID, **columns: Any) -> uuid.UUID:
    return new_job(admin, owner, analysis_slot=True, **columns)


def running_slots(admin: Connection) -> int:
    return admin.execute(
        text("SELECT count(*) FROM jobs WHERE state = 'running' AND analysis_slot")
    ).scalar_one()


def finish(admin: Connection, job_id: uuid.UUID) -> None:
    admin.execute(
        text(
            "UPDATE jobs SET state = 'completed', finished_at = now(), leased_by = NULL, "
            "lease_expires_at = NULL, heartbeat_at = NULL WHERE id = :id"
        ),
        {"id": job_id},
    )


def test_a_job_over_the_analysis_cap_stays_queued_and_others_are_claimed_past_it(
    admin: Connection, app: Engine
) -> None:
    a, b, c = new_user(admin), new_user(admin), new_user(admin)
    first = slot_job(admin, a)
    second = slot_job(admin, b)  # older than the design job: it waits, the design job does not
    design = new_job(admin, c)
    assert [r.job_id for r in claim(app)] == [first]
    assert [r.job_id for r in claim(app)] == [design]  # past the waiting analysis
    assert claim(app) == []
    assert job(admin, second).state == "queued"
    assert events(admin, second) == []  # nothing written to a job left waiting
    finish(admin, first)
    assert [r.job_id for r in claim(app)] == [second]  # a free slot: the oldest waiting one
    assert running_slots(admin) == 1


def test_the_cap_counts_running_slot_jobs_of_every_user_and_worker(
    admin: Connection, app: Engine
) -> None:
    jobs = [slot_job(admin, new_user(admin)) for _ in range(3)]
    claimed = [r.job_id for r in claim(app, worker="w1", slots=2)]
    claimed += [r.job_id for r in claim(app, worker="w2", slots=2)]
    assert claimed == jobs[:2]
    assert claim(app, worker="w3", slots=2) == []
    assert [r.job_id for r in claim(app, worker="w3", slots=3)] == [jobs[2]]  # its own cap


def test_an_expired_slot_job_holds_its_slot_until_the_reaper_requeues_it(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    held, waiting = slot_job(admin, owner), slot_job(admin, new_user(admin))
    assert [r.job_id for r in claim(app)] == [held]
    expire_lease(admin, held)
    assert claim(app) == []  # the old holder may still be analysing
    assert call(app, "SELECT public.queue_reap(10)")[0][0] == 1
    reaped = job(admin, held)
    assert (reaped.state, reaped.analysis_slot) == ("queued", True)
    assert [r.job_id for r in claim(app)] == [waiting]


def test_jobs_without_a_slot_ignore_the_cap(admin: Connection, app: Engine) -> None:
    slot_job(admin, new_user(admin))
    claim(app)
    plain = {new_job(admin, new_user(admin)) for _ in range(5)}
    claimed = {claim(app)[0].job_id for _ in range(5)}
    assert claimed == plain
    assert running_slots(admin) == 1


def test_concurrent_claimers_never_run_more_analyses_than_the_cap(
    admin: Connection, app: Engine
) -> None:
    """Sixteen claimers race over 8 analyses and 8 design jobs with a cap of 2: every design job
    is claimed, exactly 2 analyses run, and the other 6 stay queued with no event."""
    users = [new_user(admin) for _ in range(16)]
    slots = {slot_job(admin, users[i]) for i in range(8)}
    plain = {new_job(admin, users[8 + i]) for i in range(8)}
    claimed: list[uuid.UUID] = []
    lock = threading.Lock()

    def drain() -> None:
        while rows := claim(app, slots=2):
            with lock:
                claimed.append(rows[0].job_id)

    _race(16, drain)
    assert len(claimed) == len(set(claimed))
    assert plain <= set(claimed)
    assert len(set(claimed) & slots) == 2
    assert running_slots(admin) == 2
    for job_id in slots - set(claimed):
        assert job(admin, job_id).state == "queued"
        assert events(admin, job_id) == []
