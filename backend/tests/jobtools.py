"""Rows for the job-table tests (ADR 0005 section 1), written as the superuser (test setup only).

The functions under test run as ``classbeat_app``; setup bypasses RLS so a test can plant any
state, including rows the application would never write (a poison row).
"""

import hashlib
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def new_user(conn: Connection) -> uuid.UUID:
    email = f"jobs-{secrets.token_hex(6)}@example.test"
    return conn.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at) "
            "VALUES (:e, :e, now()) RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def new_class(conn: Connection, owner: uuid.UUID, title: str = "Tuesday climb") -> uuid.UUID:
    return conn.execute(
        text("INSERT INTO classes (id, owner_id, title) VALUES (:id, :o, :t) RETURNING id"),
        {"id": uuid.uuid4(), "o": owner, "t": title},
    ).scalar_one()


def new_job(
    conn: Connection,
    owner: uuid.UUID,
    class_id: uuid.UUID | None = None,
    **columns: Any,
) -> uuid.UUID:
    """A queued ``test_kind`` job (one per class, so a class is made when none is given)."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "owner_id": owner,
        "kind": "test_kind",
        "state": "queued",
        "class_id": class_id or new_class(conn, owner),
        "input": "{}",
        "idempotency_key": secrets.token_urlsafe(18),
        "request_hash": digest("request"),
        "credit_state": "reserved",
        **columns,
    }
    names = ", ".join(values)
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k == "input" else f":{k}" for k in values)
    return conn.execute(
        text(f"INSERT INTO jobs ({names}) VALUES ({params}) RETURNING id"),  # noqa: S608
        values,
    ).scalar_one()


def job(conn: Connection, job_id: uuid.UUID) -> Any:
    return conn.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).one()


def events(conn: Connection, job_id: uuid.UUID) -> list[tuple[int, str, dict[str, Any], int]]:
    rows = conn.execute(
        text(
            "SELECT seq, kind, payload, lease_version FROM job_events WHERE job_id = :id "
            "ORDER BY seq"
        ),
        {"id": job_id},
    ).all()
    return [(r.seq, r.kind, r.payload, r.lease_version) for r in rows]


@contextmanager
def engine_for(url: str, **kwargs: Any) -> Iterator[Engine]:
    engine = create_engine(url, **kwargs)
    try:
        yield engine
    finally:
        engine.dispose()


def as_user(conn: Connection, user: uuid.UUID) -> None:
    """The transaction-local ``app.user_id`` that ``user_session`` sets (ADR 0002 section 5)."""
    conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user)})
