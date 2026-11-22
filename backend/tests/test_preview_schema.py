"""Migration 0072 (#1394, ADR 0042 section 6.5): the audition's tables and the ``preview_render``
job's shape. Each constraint is shown to refuse what it must and to admit the allowed shape."""

import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

from jobtools import digest, engine_for, new_class, new_user
from test_migrations import alembic_config

_JOB = text(
    "INSERT INTO jobs (owner_id, kind, state, class_id, preview_class_id, input, "
    "idempotency_key, request_hash) VALUES (:o, :k, 'queued', :c, :p, '{}'::jsonb, :key, :h) "
    "RETURNING id"
)
_SESSION = text(
    "INSERT INTO preview_sessions (owner_id, class_id, expires_at, delivered_ms, delivered_keys) "
    "VALUES (:o, :c, now() + make_interval(mins => :m), :ms, CAST(:keys AS text[])) RETURNING id"
)
_CLIP = text(
    "INSERT INTO preview_clips (owner_id, class_id, key, start_ms, end_ms, sha256, bytes) "
    "VALUES (:o, :c, :key, :s, :e, :sha, :b) RETURNING id"
)
_SOURCE = text(
    "INSERT INTO preview_sources (owner_id, class_id, reference, sha256, bytes) "
    "VALUES (:o, :c, :r, :sha, :b) RETURNING id"
)


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def _job(conn: Connection, owner: uuid.UUID, kind: str, class_id: Any, preview: Any) -> None:
    params = {"o": owner, "k": kind, "c": class_id, "p": preview}
    conn.execute(_JOB, {**params, "key": secrets.token_urlsafe(18), "h": digest("r")})


def test_a_preview_render_has_no_class_id_and_a_preview_class(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    _job(admin, owner, "preview_render", None, class_id)
    _job(admin, owner, "preview_render", None, class_id)  # outside jobs_one_open_per_class
    _job(admin, owner, "test_kind", class_id, None)  # the class's own job slot stays free
    with pytest.raises(IntegrityError, match="jobs_preview_class"):
        _job(admin, owner, "preview_render", None, None)
    with pytest.raises(IntegrityError, match="jobs_preview_class"):
        _job(admin, owner, "preview_render", new_class(admin, owner), class_id)


@pytest.mark.parametrize("kind", ["design_draft", "studio_songs", "produce_outputs", "test_kind"])
def test_no_other_kind_has_a_null_class_or_a_preview_class(admin: Connection, kind: str) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    with pytest.raises(IntegrityError, match=r"jobs_class_id|jobs_preview_class"):
        _job(admin, owner, kind, None, class_id)
    with pytest.raises(IntegrityError, match="jobs_class_id"):
        _job(admin, owner, kind, None, None)
    with pytest.raises(IntegrityError, match="jobs_preview_class"):
        _job(admin, owner, kind, class_id, class_id)


def test_the_preview_class_is_the_owners_and_goes_with_the_class(admin: Connection) -> None:
    owner, other = new_user(admin), new_user(admin)
    theirs = new_class(admin, other)
    with pytest.raises(IntegrityError, match="jobs_preview_class_fkey"):
        _job(admin, owner, "preview_render", None, theirs)
    mine = new_class(admin, owner)
    _job(admin, owner, "preview_render", None, mine)
    admin.execute(text("DELETE FROM classes WHERE id = :c"), {"c": mine})
    left = admin.execute(text("SELECT count(*) FROM jobs WHERE owner_id = :o"), {"o": owner})
    assert left.scalar_one() == 0


def _session(conn: Connection, owner: uuid.UUID, c: uuid.UUID, **over: Any) -> None:
    conn.execute(_SESSION, {"o": owner, "c": c, "m": 30, "ms": 0, "keys": "{}", **over})


def test_a_session_lives_at_most_30_minutes_and_counts_at_most_25(admin: Connection) -> None:
    owner = new_user(admin)
    c = new_class(admin, owner)
    _session(admin, owner, c, ms=1_500_000, keys="{" + "a" * 64 + "}")
    for over, constraint in (
        ({"m": 31}, "preview_sessions_lifetime"),
        ({"ms": 1_500_001}, "preview_sessions_delivered_ms"),
        ({"ms": -1}, "preview_sessions_delivered_ms"),
        ({"keys": "{not-a-key}"}, "preview_sessions_delivered_keys"),
        ({"keys": "{" + ",".join(["b" * 64] * 201) + "}"}, "preview_sessions_delivered_keys"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            _session(admin, owner, c, **over)


def test_a_clip_is_unique_per_owner_and_key_and_at_most_6_minutes(admin: Connection) -> None:
    owner, other = new_user(admin), new_user(admin)
    c, d = new_class(admin, owner), new_class(admin, other)
    key = secrets.token_bytes(32)
    row = {"key": key, "s": 0, "e": 360_000, "sha": secrets.token_bytes(32), "b": 1000}
    admin.execute(_CLIP, {"o": owner, "c": c, **row})
    admin.execute(_CLIP, {"o": other, "c": d, **row})  # the same content key, another owner
    with pytest.raises(IntegrityError, match="preview_clips_owner_id_key_key"):
        admin.execute(_CLIP, {"o": owner, "c": c, **row})
    with pytest.raises(IntegrityError, match="preview_clips_end_ms"):
        admin.execute(
            _CLIP, {"o": owner, "c": c, **row, "key": secrets.token_bytes(32), "e": 360_001}
        )
    with pytest.raises(IntegrityError, match="preview_clips_class_fkey"):
        admin.execute(_CLIP, {"o": owner, "c": d, **row, "key": secrets.token_bytes(32)})


def test_a_source_is_one_per_class_and_reference(admin: Connection) -> None:
    owner = new_user(admin)
    c = new_class(admin, owner)
    row = {"o": owner, "c": c, "r": "trk_1", "sha": secrets.token_bytes(32), "b": 10}
    admin.execute(_SOURCE, row)
    with pytest.raises(IntegrityError, match="preview_sources_owner_id_class_id_reference_key"):
        admin.execute(_SOURCE, row)
    with pytest.raises(IntegrityError, match="preview_sources_reference"):
        admin.execute(_SOURCE, {**row, "r": "../x"})
    with pytest.raises(IntegrityError, match="preview_sources_bytes"):
        admin.execute(_SOURCE, {**row, "r": "trk_2", "b": 60 * 1024 * 1024 + 1})


def test_0072_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The downgrade deletes the ``preview_render`` jobs (the old check refuses them) and drops the
    column and the tables; the upgrade puts them back."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        with engine.begin() as conn:
            owner = new_user(conn)
            class_id = new_class(conn, owner)
            _job(conn, owner, "preview_render", None, class_id)
            _session(conn, owner, class_id)
        command.downgrade(config, "0071_mix_not_applied")
        with engine.connect() as conn:
            kinds = conn.execute(text("SELECT kind FROM jobs WHERE owner_id = :o"), {"o": owner})
            assert list(kinds.scalars()) == []
            tables = conn.execute(text("SELECT to_regclass('public.preview_sessions')"))
            assert tables.scalar_one() is None
        command.upgrade(config, "head")
        with engine.begin() as conn:
            _job(conn, owner, "preview_render", None, class_id)
    finally:
        engine.dispose()
        command.downgrade(config, "base")
