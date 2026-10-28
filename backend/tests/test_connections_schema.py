"""Migration 0003: the constraints of ``connections``, ``connection_keks`` and the TOTP forms
(ADR 0004 sections 1 and 2), checked against the database itself."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from classbeat.db import SYSTEM_TABLES
from rls_guard import check_tables
from role_guard import check_roles


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    """The superuser, for inserting rows that the constraints must refuse (no RLS)."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def user(connection: Connection) -> uuid.UUID:
    return connection.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at) "
            "VALUES ('s@example.com', 's@example.com', now()) RETURNING id"
        )
    ).scalar_one()


GOOD: dict[str, Any] = {
    "provider": "mowl",
    "status": "connected",
    "last_error": None,
    "secret": b"\x01",
    "secret_nonce": bytes(12),
    "wrapped_dek": bytes(48),
    "dek_nonce": bytes(12),
    "key_id": "kek-1",
}
INSERT = text(
    "INSERT INTO connections (owner_id, provider, status, last_error, secret, secret_nonce, "
    "wrapped_dek, dek_nonce, key_id) VALUES (:owner, :provider, :status, :last_error, :secret, "
    ":secret_nonce, :wrapped_dek, :dek_nonce, :key_id)"
)


def test_a_valid_row_is_accepted_and_one_per_provider(admin: Connection) -> None:
    owner = user(admin)
    admin.execute(INSERT, {**GOOD, "owner": owner})
    admin.execute(INSERT, {**GOOD, "owner": owner, "provider": "spotify"})
    with pytest.raises(IntegrityError, match="connections_owner_id_provider_key"):
        admin.execute(INSERT, {**GOOD, "owner": owner})
    admin.rollback()


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ({"provider": "dropbox"}, "connections_provider"),
        ({"status": "revoked"}, "connections_status"),
        ({"last_error": "MOWL said: bad password"}, "connections_last_error"),
        ({"secret_nonce": bytes(16)}, "connections_secret_nonce"),
        ({"wrapped_dek": bytes(32)}, "connections_wrapped_dek"),
        ({"dek_nonce": bytes(11)}, "connections_dek_nonce"),
        ({"key_id": "app-v1"}, "connections_key_id"),
        ({"key_id": "kek-" + "a" * 29}, "connections_key_id"),
        ({"key_id": "kek-1|x"}, "connections_key_id"),
    ],
)
def test_the_checks_refuse(admin: Connection, change: dict[str, Any], constraint: str) -> None:
    owner = user(admin)
    with pytest.raises(IntegrityError, match=constraint):
        admin.execute(INSERT, {**GOOD, "owner": owner, **change})
    admin.rollback()


@pytest.mark.parametrize(
    "category",
    [
        "invalid_credentials",
        "needs_reconnect",
        "rate_limited",
        "upstream_unavailable",
        "key_unavailable",
        "decrypt_failed",
        "not_connected",
        "unknown",
    ],
)
def test_every_category_is_a_valid_last_error(admin: Connection, category: str) -> None:
    admin.execute(INSERT, {**GOOD, "owner": user(admin), "last_error": category})
    admin.rollback()


def test_the_columns_and_defaults(admin: Connection) -> None:
    rows = admin.execute(
        text(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'connections'"
        )
    ).all()
    nullable = {r.column_name for r in rows if r.is_nullable == "YES"}
    assert nullable == {"last_error", "expires_at", "last_used_at"}
    defaults = {r.column_name: r.column_default for r in rows if r.column_default}
    assert defaults["id"] == "gen_random_uuid()"
    assert defaults["public"] == "'{}'::jsonb"
    assert set(defaults) == {"id", "public", "connected_at", "updated_at"}


def test_owned_and_system_tables_follow_adr_0002(admin: Connection) -> None:
    assert "connections" not in SYSTEM_TABLES
    assert "connection_keks" in SYSTEM_TABLES
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []


def test_connection_keks_checks(admin: Connection) -> None:
    keks = text("INSERT INTO connection_keks (key_id, check_value) VALUES (:k, :v)")
    admin.execute(keks, {"k": "kek-1", "v": bytes(16)})
    for key_id, value, constraint in (
        ("kek-2", bytes(32), "connection_keks_check_value"),
        ("bad", bytes(16), "connection_keks_key_id"),
    ):
        with pytest.raises(IntegrityError, match=constraint), admin.begin_nested():
            admin.execute(keks, {"k": key_id, "v": value})
    admin.rollback()


@pytest.mark.parametrize(
    ("key_id", "wrapped", "nonce", "ok"),
    [
        ("app-v1", None, None, True),
        ("kek-1", bytes(48), bytes(12), True),
        ("app-v1", bytes(48), bytes(12), False),
        ("kek-1", None, None, False),
        ("kek-1", bytes(32), bytes(12), False),
        ("m2-envelope", bytes(48), bytes(12), False),
    ],
)
def test_the_totp_forms(
    admin: Connection, key_id: str, wrapped: bytes | None, nonce: bytes | None, ok: bool
) -> None:
    statement = text(
        "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, wrapped_dek, dek_nonce)"
        " VALUES (:u, '\\x01', :n, :k, :w, :d)"
    )
    values = {"u": user(admin), "n": bytes(12), "k": key_id, "w": wrapped, "d": nonce}
    if ok:
        admin.execute(statement, values)
    else:
        with pytest.raises(IntegrityError, match="mfa_totp_key_form"):
            admin.execute(statement, values)
    admin.rollback()
