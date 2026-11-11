"""Fixtures for the billing tests (#948): plan prices, and a maint-role session factory for the
subscription writer. A pytest plugin (listed in `conftest.pytest_plugins`)."""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

PRICES = {"instructor": 1000, "pro": 2500}


@pytest.fixture
def priced(superuser: Connection) -> Iterator[None]:
    """Monthly prices on two plans (the migrated rows carry none), removed afterwards."""
    for plan, cents in PRICES.items():
        superuser.execute(
            text("UPDATE plans SET monthly_price_cents = :c, currency = 'USD' WHERE id = :p"),
            {"c": cents, "p": plan},
        )
    yield
    superuser.execute(text("UPDATE plans SET monthly_price_cents = NULL, currency = NULL"))


@pytest.fixture
async def maint(maint_database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on the `classbeat_maint` role, as `billing_session` opens them."""
    engine = create_async_engine(maint_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
