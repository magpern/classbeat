"""Fixtures and helpers for the admin database path (ADR 0011 sections 5 and 6, #210): the real
``classbeat_maint`` role with a random password for the run, an audit-row reader and an actor."""

import secrets
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from classbeat.admin.db import MAINT_ROLE, Actor
from conftest import url_for


@pytest.fixture
def maint_database_url(clean_auth: None, database_url: str) -> str:
    """URL of ``classbeat_maint`` (installed by ``migrated``), with a password for this test."""
    password = secrets.token_hex(16)
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f"ALTER ROLE {MAINT_ROLE} PASSWORD '{password}'"))
    finally:
        engine.dispose()
    return url_for(database_url, MAINT_ROLE, password)


@pytest.fixture
def superuser(clean_auth: None, database_url: str) -> Iterator[Connection]:
    """An autocommit superuser connection for setup and for reading the audit log."""
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def new_admin(conn: Connection) -> Actor:
    """An admin user row and the ``Actor`` the gate would build for it."""
    email = f"admin-{secrets.token_hex(6)}@example.test"
    user_id: uuid.UUID = conn.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at, role) "
            "VALUES (:e, :e, now(), 'admin') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()
    return Actor(user_id, email, f"req-{secrets.token_hex(8)}", secrets.token_bytes(32))


def audit_rows(conn: Connection, actor: Actor) -> list[tuple[str, str | None]]:
    """``(outcome, reason)`` of every audit row of ``actor``'s request, oldest first."""
    return [
        (r.outcome, r.reason)
        for r in conn.execute(
            text(
                "SELECT outcome, reason FROM admin_audit WHERE request_id = :r "
                "ORDER BY created_at, id"
            ),
            {"r": actor.request_id},
        )
    ]
