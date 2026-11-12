"""Transaction-owning sessions (ADR 0002 section 5).

``user_session`` is the only way to reach owned tables: it sets ``app.user_id`` for exactly one
transaction. It owns that transaction, so ``session.commit()`` and ``session.rollback()`` inside
it raise (a new transaction would run without the setting). What this does and does not do:

- Savepoints (``session.begin_nested()``) are allowed, and rolling one back keeps
  ``app.user_id`` (it is a transaction-level setting, not a savepoint-level one).
- Ending the transaction through any other path (``run_sync``, the connection's own
  ``rollback()``, a raw ``ROLLBACK``) is not intercepted. Rolling back **fails closed**: the
  setting is gone, so later statements on that session see no rows and cannot write owned rows.
- Committing through the connection (``(await session.connection()).commit()``) or a raw
  ``COMMIT`` would commit the partial work. That is forbidden by convention and the review gate,
  not enforced here.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, NoReturn

from sqlalchemy import Row, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import UpdateBase

from classbeat.context import UserContext, require_context
from classbeat.db.statement_policy import check_system_write

SET_USER = text("SELECT set_config('app.user_id', :user_id, true)")


class TransactionOwnedError(RuntimeError):
    """``commit()`` or ``rollback()`` was called inside ``user_session``/``system_session``."""


def _refuse(name: str) -> Any:
    async def refuse(*_: object, **__: object) -> NoReturn:
        raise TransactionOwnedError(
            f"{name}() is not allowed here: the session owns the transaction "
            "(it commits on success and rolls back on an exception)"
        )

    return refuse


class _Guard:
    """Refuses transaction control by the caller; lets the context manager finish."""

    def __init__(self, session: AsyncSession) -> None:
        self.closing = False
        self._session = session
        self._commit, self._rollback = session.commit, session.rollback
        session.commit, session.rollback = _refuse("commit"), _refuse("rollback")  # type: ignore[method-assign]
        # Also catches a commit that bypasses the async facade, for example through run_sync.
        event.listen(session.sync_session, "before_commit", self._before_commit)

    def _before_commit(self, session: Any) -> None:
        # Leaving a savepoint also fires before_commit; that is not the end of the transaction.
        if not self.closing and not session.in_nested_transaction():
            raise TransactionOwnedError(
                "commit is not allowed here: the session owns the transaction"
            )

    async def finish(self, ok: bool) -> None:
        self.closing = True
        await (self._commit if ok else self._rollback)()


# The admin path's audited maint session (``classbeat.admin.db``) owns its transaction the same way.
TransactionGuard = _Guard


@asynccontextmanager
async def _owned_session(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: str
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        await session.begin()
        guard = _Guard(session)
        try:
            # Transaction-local (third argument true): gone at commit or rollback, so a pooled
            # connection never carries it over. An empty id matches no owner.
            await session.execute(SET_USER, {"user_id": user_id})
            yield session
        except BaseException:
            await guard.finish(ok=False)
            raise
        else:
            await guard.finish(ok=True)


@asynccontextmanager
async def user_session(
    sessionmaker: async_sessionmaker[AsyncSession], ctx: UserContext
) -> AsyncIterator[AsyncSession]:
    """One transaction as ``ctx.user_id``: owned and system tables."""
    require_context(ctx)  # exactly a UserContext: no look-alike, subclass, UUID or dict
    async with _owned_session(sessionmaker, str(ctx.user_id)) as session:
        yield session


@asynccontextmanager
async def system_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One transaction with no user: system tables only; owned tables return no rows."""
    async with _owned_session(sessionmaker, "") as session:
        yield session


async def system_statement(
    sessionmaker: async_sessionmaker[AsyncSession], statement: UpdateBase
) -> Sequence[Row[Any]]:
    """One write to a system table, on the allowlist of ``statement_policy`` (anything else is
    a ``ValueError`` before the database is reached), run as its own implicit transaction
    (autocommit) with no user; returns its ``RETURNING`` rows. Postgres commits it before it
    answers, so a row lock it takes ends with the statement: no await, stalled event loop or
    cancelled task after it can hold it (#998: the limiter's upsert runs on every signed-in
    request). The statement is atomic on its own; anything that needs more than one statement
    uses ``system_session``."""
    check_system_write(statement)
    async with sessionmaker() as session:
        # No ``app.user_id`` is set (it is transaction-local and the pool's reset discards it),
        # so owned tables would return no rows, as in ``system_session``. The pool's reset
        # (``discard_session_state``) turns autocommit off again on check-in.
        connection = await session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        result = await connection.execute(statement)
        return result.all() if result.returns_rows else []
