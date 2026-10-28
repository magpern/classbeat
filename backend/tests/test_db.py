from sqlalchemy import text

from classbeat.config import Settings
from classbeat.db import create_engine, create_session_factory


def test_engine_is_lazy_and_uses_psycopg(unreachable_database_url: str) -> None:
    settings = Settings(database_url=unreachable_database_url)  # pyright: ignore[reportArgumentType]
    engine = create_engine(settings)  # would raise if it connected
    assert engine.url.drivername == "postgresql+psycopg"
    assert "not-a-real-password" not in repr(engine.url)


async def test_session_factory_runs_a_query(app_database_url: str) -> None:
    settings = Settings(database_url=app_database_url)  # pyright: ignore[reportArgumentType]
    engine = create_engine(settings, check_queue=False)  # no job tables here
    try:
        async with create_session_factory(engine)() as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()
