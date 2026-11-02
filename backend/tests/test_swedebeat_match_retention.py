"""The match revision is in the retention registry and dies with its class (ADR 0016 §5, guard 10).

``REVISION_KINDS`` names every ``class_revisions.kind`` the table allows. A planted
``swedebeat_match`` row goes when its class goes, and another user's row does not.
"""

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from classbeat.db.retention import REVISION_KINDS
from jobtools import as_user, digest, engine_for, new_class, new_user


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


def _kinds(admin: Connection) -> set[str]:
    definition = admin.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'class_revisions_kind'"
        )
    ).scalar_one()
    return set(re.findall(r"'([^']+)'", definition))


def _plant(admin: Connection, owner: object, class_id: object, title: str) -> None:
    admin.execute(
        text(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (gen_random_uuid(), :o, :c, 1, "
            "'swedebeat_match', CAST(:content AS jsonb), :hash, "
            "'{\"source\":\"swedebeat_catalogue\"}'::jsonb, '{}'::jsonb)"
        ),
        {"o": owner, "c": class_id, "content": f'{{"planted":"{title}"}}', "hash": digest(title)},
    )


def test_the_registry_names_exactly_the_kinds_the_table_allows(admin: Connection) -> None:
    assert set(REVISION_KINDS) == _kinds(admin)
    assert "swedebeat_match" in REVISION_KINDS


def test_a_planted_match_is_deleted_with_its_class_and_not_with_another_users(
    admin: Connection, app: Engine
) -> None:
    owner, other = new_user(admin), new_user(admin)
    mine, theirs = new_class(admin, owner), new_class(admin, other)
    _plant(admin, owner, mine, "PlantedSpotifyTitleQ9")
    _plant(admin, other, theirs, "OtherPlantedTitleR2")
    with app.begin() as connection:
        as_user(connection, owner)
        connection.execute(text("DELETE FROM classes WHERE id = :c"), {"c": mine})
    left = admin.execute(text("SELECT content->>'planted' FROM class_revisions")).scalars().all()
    assert left == ["OtherPlantedTitleR2"]
    with app.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": other})
    assert admin.execute(text("SELECT count(*) FROM class_revisions")).scalar_one() == 0
