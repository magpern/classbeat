from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


def applied_revisions(database_url: str) -> list[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    finally:
        engine.dispose()
    return [row[0] for row in rows]


def test_there_is_one_baseline_revision() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0001_baseline"]
    assert script.get_base() == "0001_baseline"


def test_offline_mode_renders_sql_without_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(alembic_config(), "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TABLE alembic_version" in out
    assert "0001_baseline" in out


def test_upgrade_head_then_downgrade_base(
    owner_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # env.py takes the owner role's URL from Settings, so the test points it at the test database.
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    database_url = owner_database_url
    config = alembic_config()
    try:
        command.upgrade(config, "head")
        assert applied_revisions(database_url) == ["0001_baseline"]
        command.downgrade(config, "base")
        assert applied_revisions(database_url) == []
    finally:
        command.downgrade(config, "base")
