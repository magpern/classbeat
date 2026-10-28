"""Helpers for tests that create tables as the owner role, the way migrations do."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from classbeat.db.rls import enable_owner_rls

PARENT, CHILD = "t_parent", "t_child"
OWNED_TABLES_DDL = [
    f"""CREATE TABLE {PARENT} (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL,
        name text NOT NULL,
        UNIQUE (owner_id, id),
        UNIQUE (owner_id, name))""",
    f"""CREATE TABLE {CHILD} (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL,
        parent_id uuid NOT NULL,
        UNIQUE (owner_id, id),
        FOREIGN KEY (owner_id, parent_id) REFERENCES {PARENT} (owner_id, id) ON DELETE CASCADE)""",
]


def run_rls_helper(connection: Connection, table: str) -> None:
    """Call the migration helper ``enable_owner_rls`` as an Alembic migration would."""
    with Operations.context(MigrationContext.configure(connection)):
        enable_owner_rls(table)


@contextmanager
def owner_connection(owner_url: str) -> Iterator[Connection]:
    """A transactional connection as ``classbeat_owner`` (commits on success)."""
    engine = create_engine(owner_url)
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def drop_tables(owner_url: str, *tables: str) -> None:
    with owner_connection(owner_url) as connection:
        for table in tables:
            connection.execute(text(f'DROP TABLE IF EXISTS public."{table}" CASCADE'))


BACKEND_DIR = Path(__file__).resolve().parent.parent
QUEUE_SCRIPT = BACKEND_DIR.parent / "docker" / "postgres" / "queue" / "20-queue.sql"
AUTH_TABLES = (
    "users, identities, invites, sessions, known_devices, pending_signups, auth_tokens, "
    "oauth_flows, mfa_totp, mfa_recovery_codes, rate_limits, auth_events"
)


def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@contextmanager
def migrated(owner_url: str, revision: str = "head") -> Iterator[None]:
    """The schema at ``revision`` (as the owner, like ``alembic upgrade``); removed afterwards."""
    patch = pytest.MonkeyPatch()
    patch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_url)
    try:
        _forget_v2_totp(owner_url)
        command.downgrade(alembic_config(), "base")
        command.upgrade(alembic_config(), revision)
        if revision == "head":
            # compose's db-functions step: the queue role, grants and functions (ADR 0005 s. 3).
            install_queue(os.environ["CLASSBEAT_TEST_DATABASE_URL"])
        yield
    finally:
        _forget_v2_totp(owner_url)
        command.downgrade(alembic_config(), "base")
        patch.undo()


def _forget_v2_totp(owner_url: str) -> None:
    """0003's downgrade refuses while v2 (envelope) TOTP rows exist; tests drop them first."""
    with owner_connection(owner_url) as connection:
        exists = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'mfa_totp' AND column_name = 'wrapped_dek'"
            )
        ).first()
        if exists is not None:
            connection.execute(text("DELETE FROM public.mfa_totp WHERE key_id <> 'app-v1'"))


def truncate_auth(admin_url: str) -> None:
    """Empty every auth table, and so every ``connections`` row (cascade), and the KEK check rows
    (test setup, as the superuser)."""
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f"TRUNCATE {AUTH_TABLES}, connection_keks CASCADE"))
    finally:
        engine.dispose()


def install_queue(admin_url: str) -> None:
    """Run ``docker/postgres/queue/20-queue.sql`` as the superuser in one transaction, as the
    ``db-functions`` service does with ``psql --single-transaction``. Plain SQL, no parameters
    (psycopg sends it as one simple query)."""
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            raw = connection.connection.dbapi_connection
            assert raw is not None
            cursor = raw.cursor()
            try:
                cursor.execute(QUEUE_SCRIPT.read_text(encoding="utf-8"))
            finally:
                cursor.close()
            raw.commit()
    finally:
        engine.dispose()
