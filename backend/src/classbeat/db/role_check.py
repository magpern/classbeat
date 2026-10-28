"""Startup refusal (ADR 0002 section 4): web and worker must not run as a role that sidesteps RLS.

The check runs once at startup (``verify_database_role``) and again on every new pooled
connection (``refuse_privileged_roles``), so a database that was down at startup cannot let a
privileged connection through later.
"""

import logging
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# One row of booleans for the connected role. Memberships are followed transitively, because a
# member of a BYPASSRLS or superuser role can SET ROLE to it.
ROLE_QUERY = """
WITH RECURSIVE me AS (
    SELECT oid FROM pg_roles WHERE rolname = current_user
), reachable AS (
    SELECT oid FROM me
    UNION
    SELECT am.roleid FROM pg_auth_members am JOIN reachable r ON am.member = r.oid
)
SELECT
    EXISTS (SELECT 1 FROM pg_roles x WHERE x.oid = (SELECT oid FROM me) AND x.rolsuper),
    EXISTS (SELECT 1 FROM pg_roles x WHERE x.oid = (SELECT oid FROM me) AND x.rolbypassrls),
    EXISTS (SELECT 1 FROM pg_roles x JOIN reachable r ON r.oid = x.oid
             WHERE x.oid <> (SELECT oid FROM me) AND (x.rolsuper OR x.rolbypassrls)),
    EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner = (SELECT oid FROM me)
             AND c.relkind IN ('r', 'p', 'f', 'm', 'v')),
    EXISTS (SELECT 1 FROM pg_database d WHERE d.datname = current_database()
             AND d.datdba = (SELECT oid FROM me)),
    EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner = (SELECT oid FROM me)),
    EXISTS (SELECT 1 FROM pg_roles x JOIN reachable r ON r.oid = x.oid
             WHERE x.rolname IN ('pg_execute_server_program', 'pg_read_server_files',
                                 'pg_write_server_files'))
"""
PROBLEMS = (
    "is a superuser",
    "has BYPASSRLS",
    "is a member of a superuser or BYPASSRLS role",
    "owns tables, views or materialized views",
    "owns the database",
    "owns a schema",
    "is a member of pg_execute_server_program, pg_read_server_files or pg_write_server_files",
)


class DatabaseRoleError(RuntimeError):
    """The configured database role could bypass row-level security. Never contains credentials."""


def problems_for(row: Any) -> list[str]:
    return [problem for problem, flag in zip(PROBLEMS, row, strict=True) if flag]


def _raise_if_unsafe(row: Any) -> None:
    found = problems_for(row)
    if found:
        raise DatabaseRoleError(
            "the database role used by web and worker "
            + "; it ".join(found)
            + " (ADR 0002 section 4)"
        )


def refuse_privileged_roles(engine: AsyncEngine) -> None:
    """Check every new connection of ``engine``; a privileged role is refused with an error."""

    @event.listens_for(engine.sync_engine, "connect")
    def check_connection(dbapi_connection: Any, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(ROLE_QUERY)
            row = cursor.fetchone()
        finally:
            cursor.close()
        dbapi_connection.rollback()
        _raise_if_unsafe(row)


def _is_authentication_failure(exc: OperationalError) -> bool:
    """True when the server answered and rejected the login (not when it was unreachable)."""
    text_ = str(exc.orig).lower()
    return any(
        marker in text_
        for marker in ("authentication failed", "no password supplied", "password required")
    ) or (getattr(exc.orig, "sqlstate", None) or "").startswith("28")


async def verify_database_role(engine: AsyncEngine) -> None:
    """Refuse to start when the role is privileged. An unreachable database is not an error here:
    the per-connection check still applies to every connection made later."""
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(text(ROLE_QUERY))).one()
    except OperationalError as exc:
        if _is_authentication_failure(exc):
            # Fatal: a wrong password or role is a configuration error, not a database that is
            # down. The message names no user, host or password.
            raise DatabaseRoleError(
                "the database refused the configured credentials; check database_url and the "
                "database_password secret file"
            ) from None
        logger.debug("database unreachable at startup; role checked on first connection")
        return
    _raise_if_unsafe(row)
