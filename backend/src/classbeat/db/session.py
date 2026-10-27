"""Transaction-owning sessions (ADR 0002 section 5).

``user_session`` is the only way to reach owned tables: it sets ``app.user_id`` for exactly one
transaction. It owns that transaction, so ``commit()`` and ``rollback()`` inside it raise (a
new transaction would run without the setting).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext

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

    def _before_commit(self, _session: object) -> None:
        if not self.closing:
            raise TransactionOwnedError(
                "commit is not allowed here: the session owns the transaction"
            )

    async def finish(self, ok: bool) -> None:
        self.closing = True
        await (self._commit if ok else self._rollback)()


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
    # Duck-typed on purpose: naming the class here would be a reference the AST guard refuses.
    if not isinstance(getattr(ctx, "user_id", None), UUID):
        raise TypeError("user_session needs a UserContext, not a raw user id")
    async with _owned_session(sessionmaker, str(ctx.user_id)) as session:
        yield session


@asynccontextmanager
async def system_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One transaction with no user: system tables only; owned tables return no rows."""
    async with _owned_session(sessionmaker, "") as session:
        yield session
