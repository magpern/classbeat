"""Migration 0017 (ADR 0017 section 4; P1b, #393): ``classes.dismissed_rules`` up, down and up.
Its own module: it moves the schema, which the module-scoped fixtures of the other tests keep."""

from alembic import command
from sqlalchemy import create_engine, text

from dbtools import alembic_config, migrated
from jobtools import new_class, new_user

COLUMN = (
    "SELECT 1 FROM information_schema.columns WHERE table_name = 'classes' "
    "AND column_name = 'dismissed_rules'"
)


def test_migration_0017_up_and_down(owner_database_url: str, database_url: str) -> None:
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with migrated(owner_database_url, "0016_swedebeat_flag"), engine.connect() as conn:
            owner = new_user(conn)
            old = new_class(conn, owner)  # a class from before the column
            command.upgrade(alembic_config(), "0017_dismissed_rules")
            read = text("SELECT dismissed_rules FROM classes WHERE id = :c")
            assert conn.execute(read, {"c": old}).scalar_one() == []
            nullable = text(COLUMN.replace("SELECT 1", "SELECT is_nullable"))
            assert conn.execute(nullable).scalar_one() == "NO"
            command.downgrade(alembic_config(), "0016_swedebeat_flag")
            assert conn.execute(text(COLUMN)).first() is None
            command.upgrade(alembic_config(), "0017_dismissed_rules")
            assert conn.execute(read, {"c": old}).scalar_one() == []
    finally:
        engine.dispose()
