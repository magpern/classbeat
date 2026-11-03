"""Migration 0027 (ADR 0007 amendment "beat-one corrections", BC1): the class-owned
``beat_one_corrections`` table. Row security, the checks, one row per song and music, and the
cascades with the class and the account."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from jobtools import as_user, engine_for, new_class, new_user

FP = "ab" * 32


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


INSERT = (
    "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats) "
    "VALUES (:o, :c, :f, :s, :b)"
)


def put(conn: Connection, owner: uuid.UUID, class_id: uuid.UUID, song: int = 1, beats: int = 1,
        fingerprint: str = FP) -> None:  # fmt: skip
    conn.execute(text(INSERT), {"o": owner, "c": class_id, "f": fingerprint, "s": song, "b": beats})


def rows(conn: Connection, owner: uuid.UUID) -> list[tuple[int, int]]:
    found = conn.execute(
        text("SELECT song, beats FROM beat_one_corrections WHERE owner_id = :o ORDER BY song"),
        {"o": owner},
    )
    return [(r.song, r.beats) for r in found]


def test_a_user_sees_and_changes_only_their_own_corrections(admin: Connection, app: Engine) -> None:
    mine, other = new_user(admin), new_user(admin)
    my_class, other_class = new_class(admin, mine), new_class(admin, other)
    put(admin, mine, my_class, song=1, beats=1)
    put(admin, other, other_class, song=1, beats=-2)
    with app.begin() as connection:
        as_user(connection, mine)
        seen = connection.execute(text("SELECT owner_id, beats FROM beat_one_corrections")).all()
        assert [(r.owner_id, r.beats) for r in seen] == [(mine, 1)]
        connection.execute(text("UPDATE beat_one_corrections SET beats = 3"))
        connection.execute(text("DELETE FROM beat_one_corrections WHERE owner_id = :o"),
                           {"o": other})  # fmt: skip
    assert rows(admin, other) == [(1, -2)], "the other user's row is untouched"
    assert rows(admin, mine) == [(1, 3)]


def test_a_user_cannot_write_a_correction_for_someone_elses_class(
    admin: Connection, app: Engine
) -> None:
    mine, other = new_user(admin), new_user(admin)
    other_class = new_class(admin, other)
    with app.begin() as connection:  # row security: another owner's id
        as_user(connection, mine)
        with pytest.raises(DBAPIError):
            put(connection, other, other_class)
    with app.begin() as connection:  # the owner-inclusive foreign key: another owner's class
        as_user(connection, mine)
        with pytest.raises(IntegrityError):
            put(connection, mine, other_class)
    assert rows(admin, other) == []
    assert rows(admin, mine) == []


@pytest.mark.parametrize(
    ("song", "beats", "fingerprint"),
    [(0, 1, FP), (501, 1, FP), (1, 0, FP), (1, 4, FP), (1, -4, FP), (1, 1, "AB" * 32),
     (1, 1, "ab" * 31)],
)  # fmt: skip
def test_the_checks_refuse_a_song_beats_or_fingerprint_out_of_range(
    admin: Connection, song: int, beats: int, fingerprint: str
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    with pytest.raises(IntegrityError):
        put(admin, owner, class_id, song=song, beats=beats, fingerprint=fingerprint)


def test_one_row_per_song_and_music_of_a_class(admin: Connection) -> None:
    owner = new_user(admin)
    class_id, second = new_class(admin, owner), new_class(admin, owner)
    put(admin, owner, class_id, song=2, beats=1)
    put(admin, owner, class_id, song=2, beats=-1, fingerprint="cd" * 32)  # other music
    put(admin, owner, second, song=2, beats=2)  # another class
    with pytest.raises(IntegrityError):
        put(admin, owner, class_id, song=2, beats=3)


def test_corrections_go_with_their_class_and_with_the_account(admin: Connection) -> None:
    owner = new_user(admin)
    gone, kept = new_class(admin, owner), new_class(admin, owner)
    put(admin, owner, gone, song=1)
    put(admin, owner, kept, song=2)
    admin.execute(text("DELETE FROM classes WHERE id = :c"), {"c": gone})
    assert rows(admin, owner) == [(2, 1)]
    admin.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert rows(admin, owner) == []
