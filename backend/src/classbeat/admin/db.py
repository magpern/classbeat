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
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from classbeat.admin.audit import RefusalLimiter, Target, checked_params
from classbeat.admin.maint_role import (
    MAINT_ROLE as MAINT_ROLE,  # re-exported: the tests import it from here
)
from classbeat.admin.maint_role import (
    ROLE_QUERY,
    MaintRoleError,
    check_checkout,
    check_connection,
    raise_if_wrong,
)
from classbeat.admin.principals import (  # re-exported: callers import them from here
    Actor as Actor,
)
from classbeat.admin.principals import AdminRefused as AdminRefused
from classbeat.admin.principals import Buyer as Buyer
from classbeat.admin.principals import audit_row, log_row
from classbeat.billing.errors import ACTIONS as BILLING_ACTIONS
from classbeat.billing.errors import BillingRefused
from classbeat.config import Settings
from classbeat.db import TransactionGuard
from classbeat.db.engine import CONNECT_TIMEOUT_SECONDS, discard_session_state
from classbeat.db.role_check import is_authentication_failure

logger = logging.getLogger(__name__)

POOL_SIZE: Final = 2

# The audit row's target user does not exist: the action answers 404 ``not_found`` (#218).
TARGET_USER_FK: Final = "admin_audit_target_user_id_fkey"

_READ_WRITE = text("SET TRANSACTION READ WRITE")
_READ_ONLY = text("SET TRANSACTION READ ONLY")
_INSERT_BILLING_LOG = text(
    "INSERT INTO billing_log (owner_id, action, outcome, reason, request_id) "
    "VALUES (:owner_id, :action, :outcome, :reason, :request_id)"
)
_INSERT_REFUSAL = text(
    "INSERT INTO billing_webhook_refusals (provider, event_id, reason) VALUES (:p, :e, :r) "
    "ON CONFLICT (provider, event_id) DO NOTHING"
)
_INSERT_AUDIT = text(
    "INSERT INTO admin_audit (actor_id, actor_email, action, target_user_id, target_kind, "
    "target_id, params, outcome, reason, request_id, ip_hash) VALUES (:actor_id, :actor_email, "
    ":action, :target_user_id, :target_kind, :target_id, CAST(:params AS jsonb), :outcome, "
    ":reason, :request_id, :ip_hash)"
)


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
        check_connection(dbapi_connection)

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
        raise_if_wrong(row)

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
                _INSERT_AUDIT, audit_row(actor, action, target or Target(), params, outcome, reason)
            )


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


async def _record_failure(
    db: AdminDb, actor: Actor, action: str, target: Target, params: str, exc: Exception
) -> None:
    if isinstance(exc, AdminRefused):
        exc.audited = True  # this session's refusal: the handler must not add a second row
        if not db.limiter.allow(actor.user_id):
            return
        outcome, reason = "refused", exc.reason
    else:
        outcome, reason = "error", "internal"
    try:
        await db.record(actor, action, outcome, reason, target=target, params=params)
        return
    except DataError:
        # The table refused the params themselves (a value jsonb can't hold): keep the row
        # without them, so a refused or failing action still leaves its row (#282 review).
        # Any other failure is not retried: a row without params would hide what it was.
        if params == "{}":
            logger.error("admin audit row not written", extra={"error_type": "DataError"})
            return
    except Exception as failure:  # the original exception matters more
        logger.error("admin audit row not written", extra={"error_type": type(failure).__name__})
        return
    try:
        await db.record(actor, action, outcome, reason, target=target, params="{}")
        logger.warning("admin audit row written without params", extra={"action": action})
    except Exception as failure:
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
                        _INSERT_AUDIT, audit_row(actor, action, target, encoded, "ok", None)
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


async def _record_billing_failure(db: AdminDb, buyer: Buyer, action: str, exc: Exception) -> None:
    """The ``refused`` or ``error`` row of a failed billing action, in its own short transaction."""
    outcome, reason = (
        ("refused", exc.reason) if isinstance(exc, BillingRefused) else ("error", None)
    )
    try:
        async with db._maint_factory() as session, session.begin():  # pyright: ignore[reportPrivateUsage]
            await session.execute(_READ_WRITE)
            await session.execute(_INSERT_BILLING_LOG, log_row(buyer, action, outcome, reason))
    except Exception as failure:  # the original exception matters more
        logger.error("billing log row not written", extra={"error_type": type(failure).__name__})


@asynccontextmanager
async def _logged(
    db: AdminDb, buyer: Buyer, action: str, *, write: bool
) -> AsyncIterator[AsyncSession]:
    """A maint transaction whose first statement is its ``billing_log`` row (a read then switches
    to ``READ ONLY``); a failure leaves a ``refused`` or ``error`` row of its own."""
    try:
        async with db._maint_factory() as session:  # pyright: ignore[reportPrivateUsage]
            await session.begin()
            guard = TransactionGuard(session)
            try:
                await session.execute(_READ_WRITE)
                await session.execute(_INSERT_BILLING_LOG, log_row(buyer, action, "ok", None))
                if not write:
                    await session.execute(_READ_ONLY)
                yield session
            except BaseException:
                await guard.finish(ok=False)
                raise
            else:
                await guard.finish(ok=True)
    except Exception as exc:
        await _record_billing_failure(db, buyer, action, exc)
        raise


@asynccontextmanager
async def billing_session(
    db: AdminDb, buyer: Buyer, action: str, *, write: bool = False
) -> AsyncIterator[AsyncSession]:
    """One logged transaction on the maint engine for a user's own billing action (ADR 0031
    section 3): like ``admin_session``, but its row is ``billing_log``, never ``admin_audit``.
    Called only from ``classbeat.billing.routes`` (an AST guard), after its access check."""
    if action not in BILLING_ACTIONS:
        raise ValueError(f"unknown billing action: {action}")
    async with _logged(db, buyer, action, write=write) as session:
        yield session


@asynccontextmanager
async def webhook_lookup(db: AdminDb) -> AsyncIterator[AsyncSession]:
    """The webhook's read-only maint transaction before any user is known (ADR 0032 section 3):
    the link lookup only, no log row. Called only from ``classbeat.billing.webhook``."""
    async with db._maint_factory() as session:  # pyright: ignore[reportPrivateUsage]
        await session.begin()
        guard = TransactionGuard(session)
        try:
            await session.execute(_READ_ONLY)
            yield session
        finally:
            await guard.finish(ok=False)  # nothing to keep: a read is rolled back


@asynccontextmanager
async def webhook_session(
    db: AdminDb, owner_id: UUID, request_id: str
) -> AsyncIterator[AsyncSession]:
    """One logged write transaction for a signature-verified, link-resolved notification of
    ``owner_id`` (ADR 0032 section 3): ``billing_session``'s sibling with the action ``webhook``.
    Called only from ``classbeat.billing.webhook`` (an AST guard checks it)."""
    async with _logged(db, Buyer(owner_id, request_id), "webhook", write=True) as session:
        yield session


async def record_webhook_refusal(db: AdminDb, provider: str, event_id: str, reason: str) -> None:
    """Count a refused notification once per ``(provider, event_id)``, in its own short
    transaction (Paddle's retries add nothing). Never raises: the answer matters more."""
    try:
        async with db._maint_factory() as session, session.begin():  # pyright: ignore[reportPrivateUsage]
            await session.execute(_READ_WRITE)
            await session.execute(_INSERT_REFUSAL, {"p": provider, "e": event_id, "r": reason})
    except Exception as failure:
        logger.error("webhook refusal not counted", extra={"error_type": type(failure).__name__})
