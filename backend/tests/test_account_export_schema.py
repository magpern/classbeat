"""The column-level check of the account tables in the export (ADR 0014 section 2; #349 item 3):
every column of ``users``, ``identities``, ``sessions`` and the other exported account tables is
included or excluded with a reason, and every other system table has a reason, read from the
migrated schema. A new column, or a new table, cannot be left out (or put in) unnoticed."""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.auth.export import EXCLUDE, INCLUDE, NOT_EXPORTED, PUBLIC_BYTES
from classbeat.db import SYSTEM_TABLES
from jobtools import engine_for


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


# The system tables of other packages: none holds a user's data (the guard that keeps these names
# out of ``classbeat.auth`` is the reason they are listed here and not there).
OTHER_SYSTEM_TABLES = {
    "alembic_version": "migration bookkeeping",
    "connection_keks": "key-check values of the encryption keys",
    "plans": "the same tiers for every user",
    "redeem_codes": "multi-use plan codes (hash only); the user's redemptions are exported",
    "app_settings": "operator settings, not per user",
    "admin_audit": "operator-only log (ADR 0014 section 2 and 5)",
    "model_eval_results": "recorded eval suite runs, not per user",
    "model_routes": "which model each route uses, not per user",
}


def columns(conn: Connection) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for table, column, kind in conn.execute(
        text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY ordinal_position"
        )
    ):
        found.setdefault(table, {})[column] = kind
    return found


def problems(
    live: dict[str, dict[str, str]],
    system: Mapping[str, str],
    include: dict[str, tuple[str, ...]],
    exclude: dict[str, dict[str, str]],
    not_exported: dict[str, str],
) -> list[str]:
    out: list[str] = []
    for table in sorted(system):
        if table not in include and table not in not_exported and table not in OTHER_SYSTEM_TABLES:
            out.append(f"{table}: a system table neither exported nor given a reason")
    for table in sorted(set(include) | set(exclude)):
        if table not in live:
            out.append(f"{table}: listed but not a table")
            continue
        if table not in include or table not in exclude:
            out.append(f"{table}: needs both an INCLUDE and an EXCLUDE entry")
            continue
        have = set(live[table])
        listed = set(include[table]) | set(exclude[table])
        for name in sorted(have - listed):
            out.append(f"{table}.{name}: a column neither included nor excluded")
        for name in sorted(listed - have):
            out.append(f"{table}.{name}: listed but not a column")
        for name in sorted(set(include[table]) & set(exclude[table])):
            out.append(f"{table}.{name}: both included and excluded")
        for name, reason in exclude[table].items():
            if not reason.strip():
                out.append(f"{table}.{name}: excluded without a reason")
    for table in sorted(set(OTHER_SYSTEM_TABLES) - set(system)):
        out.append(f"{table}: listed as a system table of another package but not one")
    for table in sorted(set(OTHER_SYSTEM_TABLES) & (set(include) | set(not_exported))):
        out.append(f"{table}: listed twice")
    for table in sorted(set(not_exported) & (set(include) | set(exclude))):
        out.append(f"{table}: both exported and not exported")
    for table, reason in not_exported.items():
        if table not in system:
            out.append(f"{table}: not exported but not a system table")
        if not reason.strip():
            out.append(f"{table}: not exported without a reason")
    return out


def test_every_account_column_and_system_table_is_decided(admin: Connection) -> None:
    assert problems(columns(admin), SYSTEM_TABLES, INCLUDE, EXCLUDE, NOT_EXPORTED) == []


def test_the_secret_columns_are_excluded(admin: Connection) -> None:
    live = columns(admin)
    for table, names in INCLUDE.items():
        for name in names:
            kind = live[table][name]
            # A binary column is exported only when ``PUBLIC_BYTES`` names it with its reason.
            assert kind != "bytea" or (table, name) in PUBLIC_BYTES, (table, name)
            assert not any(word in name for word in ("hash", "secret", "nonce", "dek")), name
    assert "password_hash" in EXCLUDE["users"]
    assert "token_hash" in EXCLUDE["sessions"]
    assert "public_key" in EXCLUDE["webauthn_credentials"]
    assert "sign_count" in EXCLUDE["webauthn_credentials"]


def test_public_bytes_is_exactly_the_passkey_credential_id(admin: Connection) -> None:
    """#771: the one binary column in the export is the passkey's credential id (ADR 0019,
    Consequences), each entry included, a ``bytea`` column and given a reason."""
    live = columns(admin)
    assert set(PUBLIC_BYTES) == {("webauthn_credentials", "credential_id")}
    for (table, name), reason in PUBLIC_BYTES.items():
        assert name in INCLUDE[table], name
        assert live[table][name] == "bytea", name
        assert reason.strip(), name


def test_the_check_can_fail(admin: Connection) -> None:
    admin.execute(text("ALTER TABLE users ADD COLUMN api_token text"))
    admin.execute(text("ALTER TABLE sessions ADD COLUMN note text"))
    admin.execute(text("CREATE TABLE gdpr_system_probe (id uuid)"))
    try:
        system = {**SYSTEM_TABLES, "gdpr_system_probe": "probe"}
        got = problems(columns(admin), system, INCLUDE, EXCLUDE, NOT_EXPORTED)
        assert got == [
            "gdpr_system_probe: a system table neither exported nor given a reason",
            "sessions.note: a column neither included nor excluded",
            "users.api_token: a column neither included nor excluded",
        ]
    finally:
        admin.execute(text("DROP TABLE gdpr_system_probe"))
        admin.execute(text("ALTER TABLE users DROP COLUMN api_token"))
        admin.execute(text("ALTER TABLE sessions DROP COLUMN note"))


def test_the_export_sections_read_only_columns_the_app_role_can_select(admin: Connection) -> None:
    """#948: a section of a table whose app grant is a column list (the price columns of
    ``billing_events`` and ``subscriptions``) names its columns, every one SELECTable by the app
    role, and a table with a column the app role cannot read has such a list."""
    from classbeat.account.registry import SECTIONS

    live = columns(admin)
    for section in SECTIONS:
        names = live[section.table]
        selectable = {
            name
            for name in names
            if admin.execute(
                text("SELECT has_column_privilege('classbeat_app', :t, :c, 'SELECT')"),
                {"t": section.table, "c": name},
            ).scalar_one()
        }
        assert set(section.columns) <= selectable, section.table
        if selectable != set(names):
            assert section.columns, f"{section.table} has an unreadable column but no `columns`"
