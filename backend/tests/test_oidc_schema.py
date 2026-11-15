"""Migration 0049: the provider's tables match ``classbeat.auth.oidc.schema`` and keep their rules
(ADR 0034 section 1)."""

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from classbeat.auth.oidc.schema import TABLES
from classbeat.db import SYSTEM_TABLES

H32 = hashlib.sha256(b"x").digest()
CHALLENGE = "a" * 43


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("TRUNCATE users, oidc_requests CASCADE"))
        connection.commit()
        yield connection
        connection.rollback()
    engine.dispose()


def test_the_columns_match_the_core_tables(admin: Connection) -> None:
    for table in TABLES:
        live = {
            name: nullable == "YES"
            for name, nullable in admin.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table.name},
            )
        }
        assert live == {c.name: bool(c.nullable) for c in table.columns}, table.name
        assert table.name in SYSTEM_TABLES


def test_the_tables_have_no_row_security_and_the_app_has_dml(admin: Connection) -> None:
    for table in TABLES:
        assert not admin.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table.name}
        ).scalar_one()
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert admin.execute(
                text("SELECT has_table_privilege('classbeat_app', :t, :p)"),
                {"t": f"public.{table.name}", "p": privilege},
            ).scalar_one()
        assert not admin.execute(
            text("SELECT has_table_privilege('classbeat_app', :t, 'TRUNCATE')"),
            {"t": f"public.{table.name}"},
        ).scalar_one()


def user(admin: Connection) -> Any:
    return admin.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at) "
            "VALUES ('a@x.se', 'a@x.se', now()) RETURNING id"
        )
    ).scalar_one()


def request_row(admin: Connection, **values: Any) -> None:
    row = {
        "b": H32, "r": "https://p.example/cb", "s": "s", "n": "n", "c": CHALLENGE, **values
    }  # fmt: skip
    admin.execute(
        text(
            "INSERT INTO oidc_requests (browser_hash, redirect_uri, state, nonce, code_challenge, "
            "prompt_login, expires_at) VALUES (:b, :r, :s, :n, :c, false, "
            "now() + interval '10 minutes')"
        ),
        row,
    )


def code_row(admin: Connection, owner: Any, **values: Any) -> None:
    row = {"h": H32, "u": owner, "client": "player", "c": CHALLENGE, **values}
    admin.execute(
        text(
            "INSERT INTO oidc_codes (code_hash, user_id, client_id, redirect_uri, code_challenge, "
            "nonce, auth_time, expires_at) VALUES (:h, :u, :client, 'https://p.example/cb', :c, "
            "'n', now(), now() + interval '1 minute')"
        ),
        row,
    )


@pytest.mark.parametrize(
    "values",
    [{"b": b"short"}, {"s": ""}, {"n": "n" * 513}, {"c": "a" * 42}, {"r": ""}],
)
def test_a_malformed_request_is_refused(admin: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        request_row(admin, **values)


@pytest.mark.parametrize("values", [{"h": b"short"}, {"client": "a b"}, {"c": "x"}])
def test_a_malformed_code_is_refused(admin: Connection, values: dict[str, Any]) -> None:
    owner = user(admin)
    with pytest.raises(IntegrityError):
        code_row(admin, owner, **values)


def test_a_code_hash_is_unique_and_one_grant_per_user_and_client(admin: Connection) -> None:
    owner = user(admin)
    code_row(admin, owner)
    with pytest.raises(IntegrityError):
        code_row(admin, owner)
    admin.rollback()
    owner = user(admin)
    grant = text("INSERT INTO oidc_grants (user_id, client_id) VALUES (:u, 'player')")
    admin.execute(grant, {"u": owner})
    with pytest.raises(IntegrityError):
        admin.execute(grant, {"u": owner})


def test_codes_and_grants_go_with_the_account(admin: Connection) -> None:
    owner = user(admin)
    code_row(admin, owner)
    grant = text("INSERT INTO oidc_grants (user_id, client_id) VALUES (:u, 'p')")
    admin.execute(grant, {"u": owner})
    admin.execute(text("DELETE FROM users WHERE id = :u"), {"u": owner})
    assert admin.execute(text("SELECT count(*) FROM oidc_codes")).scalar_one() == 0
    assert admin.execute(text("SELECT count(*) FROM oidc_grants")).scalar_one() == 0
