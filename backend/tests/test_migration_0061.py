"""Migration 0061 (ADR 0038 section 7, #1189): ``jobs.approval_save`` and ``classes.save_pending``
exist at head with their defaults and the CHECK on ``save_pending``'s shape, and the downgrade
removes both. Never the network."""

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


def _columns(url: str) -> set[tuple[str, str]]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name IN "
                    "('approval_save', 'save_pending')"
                )
            ).all()
    finally:
        engine.dispose()
    return {(r[0], r[1]) for r in rows}


def test_0061_adds_the_receipt_save_and_save_pending_and_the_downgrade_removes_them(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = _config()
    admin = create_engine(database_url)
    owner, class_id = uuid.uuid4(), uuid.uuid4()
    try:
        command.upgrade(config, "0060_class_pending_messages")
        assert _columns(database_url) == set()
        command.upgrade(config, "0061_composer_save")
        assert _columns(database_url) == {("jobs", "approval_save"), ("classes", "save_pending")}
        with admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, email_normalized, email_verified_at) "
                    "VALUES (:o, :e, :e, now())"
                ),
                {"o": owner, "e": f"m61-{owner.hex[:8]}@example.com"},
            )
            connection.execute(
                text("INSERT INTO classes (id, owner_id, title) VALUES (:c, :o, 't')"),
                {"c": class_id, "o": owner},
            )
            default = connection.execute(
                text("SELECT save_pending FROM classes WHERE id = :c"), {"c": class_id}
            ).scalar()
            assert default is None
        good = {"revision_id": str(uuid.uuid4()), "content_hash": "ab" * 32, "job_id": "x"}
        put = text("UPDATE classes SET save_pending = CAST(:v AS jsonb) WHERE id = :c")
        with admin.begin() as connection:
            connection.execute(put, {"v": json.dumps(good), "c": class_id})
        for bad in (
            {"revision_id": good["revision_id"], "content_hash": good["content_hash"]},
            [good],
            {**good, "pad": "x" * 600},
        ):
            with pytest.raises(IntegrityError), admin.begin() as connection:
                connection.execute(put, {"v": json.dumps(bad), "c": class_id})
        command.downgrade(config, "0060_class_pending_messages")
        assert _columns(database_url) == set()
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
        admin.dispose()
        command.downgrade(config, "base")
