"""The admin database path (ADR 0011 section 5): the only code that builds a session on the
``classbeat_maint`` engine (guard 11's source check, ``tests/test_maint_privileges.py``).

**The role.** ``classbeat_maint`` (``docker/postgres/maint/30-maint.sql``) is ``BYPASSRLS`` with
``SELECT`` on an allowlist of columns only, and its transactions are read only by default
(``default_transaction_read_only = on``). Web checks the role at startup and on every new
connection: exactly that role, ``BYPASSRLS``, ``NOSUPERUSER NOCREATEDB NOCREATEROLE
NOREPLICATION NOINHERIT``, a member of no role and with no members, owning nothing, and exactly
its four role settings. A wrong role is a ``MaintRoleError``: web refuses to start.

**The pool** is small (2, no overflow) and lazy. On check-in a connection is rolled back and
``DISCARD ALL`` resets everything session level (``SET default_transaction_read_only = off``,
``SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE``, temp tables, held cursors). On checkout
it is checked again (the read-only default and the timeouts) and invalidated when it
differs. Both checks run in their own transaction, ended before the connection is handed out,
and there is no pre-ping: the first statement of ``admin_session`` is ``SET TRANSACTION READ
WRITE``, which Postgres allows only before any query of the transaction.

**Every transaction is audited.** ``admin_session`` runs ``SET TRANSACTION READ WRITE`` and
inserts its ``admin_audit`` row (``outcome = 'ok'``) as its first statement. A read
(``write=False``) then runs ``SET TRANSACTION READ ONLY``: Postgres allows switching to read only
after a write and refuses the way back (``transaction read-write mode must be set before any
query``), so nothing the read path does can write, and the audit row commits with it. A write
yields read-write; its audit row commits or rolls back with the action. When the body raises,
the transaction rolls back (the ``ok`` row with it) and one row with ``outcome = 'refused'``
(``AdminRefused``) or ``'error'`` is written in its own short transaction. The caller cannot
commit or roll back the session.

**An unknown target user.** The audit row is the first statement, so a ``target`` naming a user
that does not exist fails its foreign key there: ``admin_session`` then raises ``AdminRefused``
404 ``not_found`` and records it with the id as text (``target_id``) and no ``target_user_id``.
"""

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Final
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DisconnectionError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from classbeat.admin.audit import RefusalLimiter, Target, checked_params
from classbeat.config import Settings
from classbeat.db import TransactionGuard
from classbeat.db.engine import CONNECT_TIMEOUT_SECONDS, discard_session_state
from classbeat.db.role_check import (
    EXPECTED_ROLE_SETTINGS,
    is_authentication_failure,
)

logger = logging.getLogger(__name__)

MAINT_ROLE: Final = "classbeat_maint"
POOL_SIZE: Final = 2
# The app role's three timeouts plus the read-only default (ADR 0011 section 5).
EXPECTED_MAINT_SETTINGS: Final = {**EXPECTED_ROLE_SETTINGS, "default_transaction_read_only": "on"}

ROLE_QUERY: Final = """
SELECT current_user::text, r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole,
       r.rolreplication, r.rolinherit, r.rolcanlogin,
       (SELECT count(*) FROM pg_catalog.pg_auth_members m WHERE r.oid IN (m.member, m.roleid)),
       ARRAY(SELECT s.setdatabase::text || ':' || c FROM pg_catalog.pg_db_role_setting s,
                    unnest(s.setconfig) c WHERE s.setrole = r.oid ORDER BY 1),
       EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.relowner = r.oid)
         OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspowner = r.oid)
         OR EXISTS (SELECT 1 FROM pg_catalog.pg_database d WHERE d.datdba = r.oid)
         OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p WHERE p.proowner = r.oid)
  FROM pg_catalog.pg_roles r WHERE r.rolname = current_user
"""
CHECKOUT_QUERY: Final = """
SELECT current_setting('default_transaction_read_only'), current_setting('transaction_read_only'),
       ARRAY(SELECT name || '=' || setting FROM pg_catalog.pg_settings WHERE name IN
             ('statement_timeout', 'lock_timeout', 'idle_in_transaction_session_timeout')
             ORDER BY 1)
"""
EXPECTED_TIMEOUTS_MS: Final = [
    "idle_in_transaction_session_timeout=60000",
    "lock_timeout=5000",
    "statement_timeout=30000",
]

# The audit row's target user does not exist: the action answers 404 ``not_found`` (#218).
TARGET_USER_FK: Final = "admin_audit_target_user_id_fkey"

_READ_WRITE = text("SET TRANSACTION READ WRITE")
_READ_ONLY = text("SET TRANSACTION READ ONLY")
_INSERT_AUDIT = text(
    "INSERT INTO admin_audit (actor_id, actor_email, action, target_user_id, target_kind, "
    "target_id, params, outcome, reason, request_id, ip_hash) VALUES (:actor_id, :actor_email, "
    ":action, :target_user_id, :target_kind, :target_id, CAST(:params AS jsonb), :outcome, "
    ":reason, :request_id, :ip_hash)"
)


class MaintRoleError(RuntimeError):
    """The admin database role is not exactly ``classbeat_maint`` as ADR 0011 section 5 sets it.
    Never contains credentials."""


class AdminRefused(Exception):
    """An admin action refused for a reason the admin may see (409 ``target_is_admin``, ...):
    ``admin_session`` records it as ``outcome = 'refused'`` and re-raises it."""

    def __init__(self, reason: str, status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class Actor:
    """Who acts, for the audit row: the admin's id and email (at the time), the request's id and
    an HMAC of its IP address. Built only by the admin gate (``classbeat.admin.routes``)."""

    __slots__ = ("email", "ip_hash", "request_id", "user_id")

    def __init__(self, user_id: UUID, email: str, request_id: str, ip_hash: bytes) -> None:
        if len(ip_hash) != 32:
            raise ValueError("ip_hash must be 32 bytes")
        self.user_id, self.email = user_id, email
        self.request_id, self.ip_hash = request_id, ip_hash


def role_problems(row: Any) -> list[str]:
    """Problems for one ``ROLE_QUERY`` row; empty when the role is exactly classbeat_maint."""
    if row is None:
        return ["the connected role does not exist in pg_roles"]
    (
        user,
        superuser,
        bypass,
        createdb,
        createrole,
        replication,
        inherit,
        login,
        members,
        settings,
        owns,
    ) = row
    found: list[str] = []
    if user != MAINT_ROLE:
        found.append(f"connected as another role than {MAINT_ROLE}")
    if superuser:
        found.append("is a superuser")
    if not bypass:
        found.append("lacks BYPASSRLS")
    if createdb or createrole or replication:
        found.append("may create databases or roles or replicate")
    if inherit:
        found.append("inherits")
    if not login:
        found.append("cannot log in")
    if members:
        found.append("is a member of a role or has members")
    expected = sorted(f"0:{k}={v}" for k, v in EXPECTED_MAINT_SETTINGS.items())
    if sorted(settings) != expected:
        found.append("has role settings other than the three timeouts and the read-only default")
    if owns:
        found.append("owns a relation, schema, database or function")
    return found


def _raise_if_wrong(row: Any) -> None:
    found = role_problems(row)
    if found:
        raise MaintRoleError(
            "the admin database role " + "; it ".join(found) + " (ADR 0011 section 5)"
        )


def check_checkout(dbapi_connection: Any) -> None:
    """A pooled maint connection as the role defaults leave it, or ``DisconnectionError`` (the
    pool then opens a fresh one). Runs in its own transaction and rolls it back."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(CHECKOUT_QUERY)
        default_ro, current_ro, timeouts = cursor.fetchone()
    finally:
        cursor.close()
    dbapi_connection.rollback()
    if default_ro != "on" or current_ro != "on" or list(timeouts) != EXPECTED_TIMEOUTS_MS:
        raise DisconnectionError("a pooled admin connection carries state from an earlier use")


def _check_connection(dbapi_connection: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(ROLE_QUERY)
        row = cursor.fetchone()
    finally:
        cursor.close()
    dbapi_connection.rollback()
    _raise_if_wrong(row)


def create_maint_engine(url: URL) -> AsyncEngine:
    """The maint engine: lazy, 2 connections, no overflow, checked on connect and checkout,
    reset with ``DISCARD ALL`` on check-in (``classbeat.db.engine.discard_session_state``)."""
    engine = create_async_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=False,  # a ping would be the transaction's first query
        pool_reset_on_return=None,  # discard_session_state does the rollback itself
        hide_parameters=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_connection: Any, _record: object) -> None:
        _check_connection(dbapi_connection)

    @event.listens_for(engine.sync_engine, "checkout")
    def on_checkout(dbapi_connection: Any, _record: object, _proxy: object) -> None:
        check_checkout(dbapi_connection)

    discard_session_state(engine)
    return engine


class AdminDb:
    """The admin area's database: the maint engine, its sessions and the refusal limiter."""

    def __init__(self, url: URL, limiter: RefusalLimiter | None = None) -> None:
        self._maint_engine = create_maint_engine(url)
        self._maint_factory = async_sessionmaker(self._maint_engine, expire_on_commit=False)
        self.limiter = limiter or RefusalLimiter()

    @classmethod
    def from_settings(cls, settings: Settings) -> "AdminDb | None":
        """``None`` without the maint URL or the ``database_maint_password`` secret file: the
        admin routes then answer 503 ``admin_unavailable``."""
        url = settings.maint_sqlalchemy_url()
        return cls(url) if url is not None else None

    async def verify(self) -> None:
        """At startup: refuse a wrong role (``MaintRoleError``). An unreachable database is not
        fatal here; every new connection is checked when it is made."""
        try:
            async with self._maint_engine.connect() as connection:
                row = (await connection.execute(text(ROLE_QUERY))).one()
        except MaintRoleError:
            raise
        except OperationalError as exc:
            if is_authentication_failure(exc):
                raise MaintRoleError(
                    "the database refused the admin role's credentials; check "
                    "maint_database_url and the database_maint_password secret file"
                ) from None
            logger.debug("database unreachable at startup; the admin role is checked later")
            return
        _raise_if_wrong(row)

    async def dispose(self) -> None:
        await self._maint_engine.dispose()

    async def record(
        self,
        actor: Actor,
        action: str,
        outcome: str,
        reason: str | None,
        *,
        target: Target | None = None,
        params: str = "{}",
    ) -> None:
        """One audit row in its own short transaction (a refusal or an error)."""
        async with self._maint_factory() as session, session.begin():
            await session.execute(_READ_WRITE)
            await session.execute(
                _INSERT_AUDIT, _row(actor, action, target or Target(), params, outcome, reason)
            )


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _row(
    actor: Actor, action: str, target: Target, params: str, outcome: str, reason: str | None
) -> dict[str, Any]:
    return {
        "actor_id": actor.user_id,
        "actor_email": actor.email,
        "action": action,
        "target_user_id": target.user_id,
        "target_kind": target.kind,
        "target_id": target.id,
        "params": params,
        "outcome": outcome,
        "reason": reason,
        "request_id": actor.request_id,
        "ip_hash": actor.ip_hash,
    }


async def _record_failure(
    db: AdminDb, actor: Actor, action: str, target: Target, params: str, exc: Exception
) -> None:
    if isinstance(exc, AdminRefused):
        if not db.limiter.allow(actor.user_id):
            return
        outcome, reason = "refused", exc.reason
    else:
        outcome, reason = "error", "internal"
    try:
        await db.record(actor, action, outcome, reason, target=target, params=params)
    except Exception as failure:  # the original exception matters more
        logger.error("admin audit row not written", extra={"error_type": type(failure).__name__})


@asynccontextmanager
async def admin_session(
    db: AdminDb,
    actor: Actor,
    action: str,
    *,
    write: bool = False,
    target: Target | None = None,
    params: Mapping[str, Any] | None = None,
) -> AsyncIterator[AsyncSession]:
    """One audited transaction on the maint engine (module docstring). ``write=True`` only after
    the step-up check (the admin gate does it for every non-GET route)."""
    encoded = checked_params(action, params)  # before any connection: a bug is not audited
    target = target or Target()
    try:
        async with db._maint_factory() as session:  # pyright: ignore[reportPrivateUsage]
            await session.begin()
            guard = TransactionGuard(session)
            try:
                await session.execute(_READ_WRITE)
                try:
                    await session.execute(
                        _INSERT_AUDIT, _row(actor, action, target, encoded, "ok", None)
                    )
                except IntegrityError as exc:
                    if target.user_id is None or _constraint(exc) != TARGET_USER_FK:
                        raise
                    # No such user: 404, audited with the id as text (#218).
                    target = Target(kind=target.kind or "user", id=target.id or str(target.user_id))
                    raise AdminRefused("not_found", 404) from None
                if not write:
                    await session.execute(_READ_ONLY)
                yield session
            except BaseException:
                await guard.finish(ok=False)
                raise
            else:
                await guard.finish(ok=True)
    except Exception as exc:
        await _record_failure(db, actor, action, target, encoded, exc)
        raise


async def audit_refusal(
    db: AdminDb,
    actor: Actor,
    action: str,
    reason: str,
    *,
    target: Target | None = None,
    params: Mapping[str, Any] | None = None,
) -> bool:
    """A ``refused`` row for an admin's refused request (ADR 0011 section 6), within the
    limiter's 20 per admin and 100 per minute. ``False`` when the limiter or the database
    dropped it (logged)."""
    encoded = checked_params(action, params)
    if not db.limiter.allow(actor.user_id):
        return False
    try:
        await db.record(actor, action, "refused", reason, target=target, params=encoded)
    except Exception as exc:
        logger.error("admin refusal row not written", extra={"error_type": type(exc).__name__})
        return False
    return True
