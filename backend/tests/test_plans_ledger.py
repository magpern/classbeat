"""Migration 0007's credit ledger and audit log (ADR 0011 section 1; issue DB1, #207): the
restrictive policies, the ledger trigger by role (app, a maint stand-in, the owner), the cascades
of a deleted class and account, and the append-only audit log."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from jobtools import digest, engine_for, new_class, new_job, new_user
from test_plans_schema import MAINT, REFUSED, as_owner_of, ledger, row, update_as


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


@pytest.fixture
def owner_engine(owner_database_url: str) -> Iterator[Engine]:
    with engine_for(owner_database_url) as engine:
        yield engine


@pytest.fixture
def maint(admin: Connection) -> Iterator[Connection]:
    """A stand-in for ``classbeat_maint`` (AR1 creates the real one): BYPASSRLS and the ledger
    columns ADR 0011 section 5 grants it, reached by ``SET ROLE`` from the superuser."""
    created = not admin.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": MAINT}
    ).first()
    if created:
        admin.execute(text(f"CREATE ROLE {MAINT} NOLOGIN NOSUPERUSER BYPASSRLS NOINHERIT"))
        admin.execute(text(f"GRANT USAGE ON SCHEMA public TO {MAINT}"))
        admin.execute(
            text(
                f"GRANT SELECT, UPDATE (state, reason, settled_at, refunded_by) "
                f"ON public.credit_ledger TO {MAINT}"
            )
        )
    try:
        yield admin
    finally:
        admin.execute(text("RESET ROLE"))
        if created:
            admin.execute(text(f"REVOKE ALL ON public.credit_ledger FROM {MAINT}"))
            admin.execute(text(f"REVOKE ALL ON SCHEMA public FROM {MAINT}"))
            admin.execute(text(f"DROP ROLE {MAINT}"))


# The credit ledger.


def test_the_app_role_inserts_only_reserved_ledger_rows_and_never_deletes(
    admin: Connection, app: Engine
) -> None:
    user = new_user(admin)
    insert = (
        "INSERT INTO credit_ledger (owner_id, job_id, period, plan_id, state, settled_at) "
        "VALUES (:o, :j, CAST(date_trunc('month', now()) AS date), 'free', :s, :at)"
    )
    for state, settled in (("committed", None), ("released", None), ("refunded", None)):
        with pytest.raises(DBAPIError, match="row-level security"):
            update_as(app, user, insert, o=user, j=new_job(admin, user), s=state, at=settled)
    with pytest.raises(DBAPIError, match="row-level security"):
        update_as(app, user, insert, o=user, j=new_job(admin, user), s="reserved", at="2026-10-01")
    job_id = new_job(admin, user)
    assert update_as(app, user, insert, o=user, j=job_id, s="reserved", at=None) == 1
    with pytest.raises(IntegrityError, match="credit_ledger_owner_id_job_id_key"):
        update_as(app, user, insert, o=user, j=job_id, s="reserved", at=None)
    with pytest.raises(DBAPIError, match="permission denied"):
        update_as(app, user, "DELETE FROM credit_ledger")
    assert admin.execute(text("SELECT count(*) FROM credit_ledger")).scalar_one() == 1


SETTLE = "UPDATE credit_ledger SET state = :s, reason = :r, settled_at = now() WHERE id = :i"


@pytest.mark.parametrize(("state", "reason"), [("committed", None), ("released", "cancelled")])
def test_the_app_role_settles_a_reservation_once(
    admin: Connection, app: Engine, state: str, reason: str | None
) -> None:
    user = new_user(admin)
    entry = ledger(admin, user, new_job(admin, user))
    assert update_as(app, user, SETTLE, s=state, r=reason, i=entry) == 1
    assert (row(admin, entry).state, row(admin, entry).reason) == (state, reason)
    # Settled is final for the app: the policy hides it from every further update.
    for target in ("reserved", "committed", "released", "refunded"):
        assert update_as(app, user, SETTLE, s=target, r=None, i=entry) == 0
    assert row(admin, entry).state == state


def test_the_app_role_changes_nothing_else_on_the_ledger(admin: Connection, app: Engine) -> None:
    user = new_user(admin)
    entry = ledger(admin, user, new_job(admin, user))
    other_job = new_job(admin, user)
    for statement, params, match in (
        (
            "UPDATE credit_ledger SET state = 'refunded' WHERE id = :i",
            {},
            REFUSED,
        ),  # the trigger, before the policy's check
        (
            "UPDATE credit_ledger SET job_id = NULL WHERE id = :i",
            {},
            "row-level security",
        ),  # the trigger lets it by; the policy not
        (
            "UPDATE credit_ledger SET state = 'committed', job_id = NULL WHERE id = :i",
            {},
            REFUSED,
        ),
        (
            "UPDATE credit_ledger SET state = 'committed', job_id = :j WHERE id = :i",
            {"j": other_job},
            REFUSED,
        ),
        (
            "UPDATE credit_ledger SET state = 'released', plan_id = 'pro' WHERE id = :i",
            {},
            REFUSED,
        ),
        (
            "UPDATE credit_ledger SET state = 'committed', "
            "period = CAST(date_trunc('month', now()) - interval '1 month' AS date) WHERE id = :i",
            {},
            REFUSED,
        ),
        (
            "UPDATE credit_ledger SET state = 'committed', refunded_by = :o WHERE id = :i",
            {"o": user},
            REFUSED,
        ),
    ):
        with pytest.raises(DBAPIError, match=match):
            update_as(app, user, statement, i=entry, **params)
    assert row(admin, entry).state == "reserved"


def test_the_owner_role_can_settle_or_refund_nothing(
    admin: Connection, owner_engine: Engine
) -> None:
    user = new_user(admin)
    reserved = ledger(admin, user, new_job(admin, user))
    committed = ledger(
        admin, user, new_job(admin, user), state="committed", settled_at="2026-10-02"
    )
    with pytest.raises(DBAPIError, match=REFUSED):
        update_as(owner_engine, user, SETTLE, s="committed", r=None, i=reserved)
    # Outside row security too (the owner is exempt once FORCE is off): the trigger still refuses.
    with as_owner_of(owner_engine, user) as connection:
        connection.execute(text("ALTER TABLE credit_ledger NO FORCE ROW LEVEL SECURITY"))
        with pytest.raises(DBAPIError, match=REFUSED), connection.begin_nested():
            connection.execute(
                text("UPDATE credit_ledger SET state = 'refunded' WHERE id = :i"), {"i": committed}
            )
        connection.execute(text("ALTER TABLE credit_ledger FORCE ROW LEVEL SECURITY"))
    assert (row(admin, reserved).state, row(admin, committed).state) == ("reserved", "committed")


def test_maint_refunds_a_committed_credit_and_never_releases_a_reservation(
    maint: Connection,
) -> None:
    user = new_user(maint)
    reserved = ledger(maint, user, new_job(maint, user))
    committed = ledger(
        maint, user, new_job(maint, user), state="committed", settled_at="2026-10-02"
    )
    released = ledger(maint, user, new_job(maint, user), state="released", settled_at="2026-10-02")
    maint.execute(text(f"SET ROLE {MAINT}"))
    refund = (
        "UPDATE credit_ledger SET state = 'refunded', reason = 'admin_refund', "
        "refunded_by = :by WHERE id = :i"
    )
    for target, statement in (
        (reserved, "UPDATE credit_ledger SET state = 'released', reason = 'failed' WHERE id = :i"),
        (reserved, "UPDATE credit_ledger SET state = 'committed' WHERE id = :i"),
        (reserved, refund),
        (released, refund),
        (committed, "UPDATE credit_ledger SET state = 'released' WHERE id = :i"),
    ):
        with pytest.raises(DBAPIError, match=REFUSED):
            maint.execute(text(statement), {"i": target, "by": user})
    maint.execute(text(refund), {"i": committed, "by": user})
    maint.execute(text("RESET ROLE"))
    assert row(maint, committed).state == "refunded"
    assert row(maint, committed).refunded_by == user
    assert (row(maint, reserved).state, row(maint, released).state) == ("reserved", "released")
    maint.execute(text(f"SET ROLE {MAINT}"))
    with pytest.raises(DBAPIError, match=REFUSED):  # refunded is final
        maint.execute(
            text("UPDATE credit_ledger SET state = 'committed' WHERE id = :i"), {"i": committed}
        )


def test_deleting_a_class_keeps_its_ledger_rows_and_deleting_the_user_removes_them(
    admin: Connection, app: Engine
) -> None:
    user, other = new_user(admin), new_user(admin)
    class_id = new_class(admin, user)
    job_id = new_job(admin, user, class_id)
    committed = ledger(admin, user, job_id, state="committed", settled_at="2026-10-02")
    reserved = ledger(admin, user, new_job(admin, user, new_class(admin, user)))
    kept = ledger(admin, other, new_job(admin, other))
    # The class API deletes as the app role; the cascade runs as the table owner.
    with as_owner_of(app, user) as connection:
        assert (
            connection.execute(text("DELETE FROM classes WHERE id = :c"), {"c": class_id}).rowcount
            == 1
        )
    gone = row(admin, committed)
    assert (gone.job_id, gone.state, gone.settled_at is not None) == (None, "committed", True)
    assert row(admin, reserved).job_id is not None
    admin.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
    assert admin.execute(
        text("SELECT id FROM credit_ledger WHERE id IN (:a, :b, :c)"),
        {"a": committed, "b": reserved, "c": kept},
    ).scalars().all() == [kept]


# The admin audit log.


def test_the_audit_log_is_append_only_but_keeps_rows_of_a_deleted_admin(admin: Connection) -> None:
    actor, target = new_user(admin), new_user(admin)
    audit_id = admin.execute(
        text(
            "INSERT INTO admin_audit (actor_id, actor_email, action, target_user_id, outcome, "
            "request_id, ip_hash) VALUES (:a, 'admin@example.test', 'users.disable', :t, 'ok', "
            "'req-1', :h) RETURNING id"
        ),
        {"a": actor, "t": target, "h": digest("ip")},
    ).scalar_one()
    for statement in (
        "UPDATE admin_audit SET outcome = 'error' WHERE id = :i",
        "UPDATE admin_audit SET actor_id = NULL, outcome = 'refused' WHERE id = :i",
        "DELETE FROM admin_audit WHERE id = :i",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            admin.execute(text(statement), {"i": audit_id})
    with pytest.raises(DBAPIError, match="append-only"):
        admin.execute(
            text("UPDATE admin_audit SET actor_id = :t WHERE id = :i"), {"i": audit_id, "t": target}
        )
    admin.execute(text("DELETE FROM users WHERE id IN (:a, :t)"), {"a": actor, "t": target})
    kept = admin.execute(text("SELECT * FROM admin_audit WHERE id = :i"), {"i": audit_id}).one()
    assert (kept.actor_id, kept.target_user_id, kept.actor_email) == (
        None,
        None,
        "admin@example.test",
    )


def test_the_trigger_functions_are_security_invoker_and_not_callable(admin: Connection) -> None:
    rows = admin.execute(
        text(
            "SELECT proname, prosecdef, has_function_privilege('classbeat_app', oid, 'EXECUTE') "
            "FROM pg_proc WHERE proname IN ('credit_ledger_guard', 'admin_audit_guard') "
            "ORDER BY proname"
        )
    ).all()
    assert [tuple(r) for r in rows] == [
        ("admin_audit_guard", False, False),
        ("credit_ledger_guard", False, False),
    ]
