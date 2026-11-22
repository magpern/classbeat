"""Migration 0078 (#1398, ADR 0042 section 7.4): ``spotify_offset_corrections``, class-owned as
``beat_one_corrections`` is. Each constraint is shown to refuse what it must and to admit the
allowed shape; the rows go with the class and the account; the owner policy hides another user's
rows; and the table holds no Spotify id and no title (its columns are all it keeps)."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from jobtools import engine_for, new_class, new_user

SHA = "ab" * 32
AGAINST = {"map_version": 3, "sha256": "cd" * 32}
_ROW = text(
    "INSERT INTO spotify_offset_corrections "
    "(owner_id, class_id, fingerprint, song, offset_ms, set_against_analysis) "
    "VALUES (:o, :c, :f, :s, :ms, CAST(:a AS jsonb)) RETURNING id"
)


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def _row(conn: Connection, owner: uuid.UUID, class_id: Any, **more: Any) -> Any:
    params: dict[str, Any] = {"f": SHA, "s": 1, "ms": 250, "a": AGAINST, **more}
    params["a"] = json.dumps(params["a"])
    return conn.execute(_ROW, {"o": owner, "c": class_id, **params}).scalar_one()


def test_a_correction_is_one_per_song_of_a_class_and_its_music(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    _row(admin, owner, class_id)
    _row(admin, owner, class_id, s=2)  # another song
    _row(admin, owner, class_id, f="cd" * 32)  # the same song of other music
    _row(admin, owner, new_class(admin, owner))  # the same song and music of another class
    with pytest.raises(IntegrityError, match="spotify_offset_corrections_one_per_song"):
        _row(admin, owner, class_id)


@pytest.mark.parametrize(
    ("more", "constraint"),
    [
        ({"s": 0}, "spotify_offset_corrections_song"),
        ({"s": 201}, "spotify_offset_corrections_song"),
        ({"ms": 0}, "spotify_offset_corrections_offset_ms"),
        ({"ms": 60_001}, "spotify_offset_corrections_offset_ms"),
        ({"ms": -60_001}, "spotify_offset_corrections_offset_ms"),
        ({"f": "not-a-fingerprint"}, "spotify_offset_corrections_fingerprint"),
        ({"a": {"sha256": SHA}}, "spotify_offset_corrections_set_against"),  # no map_version
        ({"a": {"map_version": 1}}, "spotify_offset_corrections_set_against"),  # no sha256
        ({"a": {"map_version": 1, "sha256": "x"}}, "spotify_offset_corrections_set_against"),
        ({"a": {"map_version": "1", "sha256": SHA}}, "spotify_offset_corrections_set_against"),
        (
            {"a": {"map_version": 1, "sha256": SHA, "title": "A Song"}},  # nothing else is kept
            "spotify_offset_corrections_set_against",
        ),
        ({"a": [1]}, "spotify_offset_corrections_set_against"),
    ],
)
def test_the_checks_refuse_what_a_correction_is_not(
    admin: Connection, more: dict[str, Any], constraint: str
) -> None:
    owner = new_user(admin)
    with pytest.raises(IntegrityError, match=constraint):
        _row(admin, owner, new_class(admin, owner), **more)


def test_the_checks_admit_the_bounds_and_a_map_without_a_version(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    _row(admin, owner, class_id, s=1, ms=60_000)
    _row(admin, owner, class_id, s=200, ms=-60_000, a={"map_version": None, "sha256": SHA})


def test_a_correction_needs_a_class_of_the_same_owner(admin: Connection) -> None:
    owner, other = new_user(admin), new_user(admin)
    with pytest.raises(IntegrityError, match="spotify_offset_corrections_class_fkey"):
        _row(admin, owner, new_class(admin, other))  # another user's class
    with pytest.raises(IntegrityError, match="spotify_offset_corrections_class_fkey"):
        _row(admin, owner, uuid.uuid4())  # no class


def test_the_rows_go_with_the_class_and_with_the_account(admin: Connection) -> None:
    owner = new_user(admin)
    kept, gone = new_class(admin, owner), new_class(admin, owner)
    _row(admin, owner, kept)
    _row(admin, owner, gone)
    count = text("SELECT count(*) FROM spotify_offset_corrections WHERE owner_id = :o")
    admin.execute(text("DELETE FROM classes WHERE id = :c"), {"c": gone})
    assert admin.execute(count, {"o": owner}).scalar_one() == 1
    admin.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert admin.execute(count, {"o": owner}).scalar_one() == 0


def test_the_table_keeps_no_spotify_id_and_no_title(admin: Connection) -> None:
    columns = set(
        admin.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'spotify_offset_corrections'"
            )
        ).scalars()
    )
    assert columns == {
        "id", "owner_id", "class_id", "fingerprint", "song", "offset_ms",
        "set_against_analysis", "created_at", "updated_at",
    }  # fmt: skip
