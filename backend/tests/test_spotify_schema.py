"""Migration 0006 (ADR 0008 section 1, ADR 0009 sections 3 and 5; issue SP1, #174): the Spotify
flow, pool and class-playlist tables, ``connections.generation``, the new kinds and categories.
Contract, isolation, cascades, the guards and the migration itself. The application's deletion
paths (``spotify_connection``) and the generation guard are in ``test_spotify_forget.py`` and
``test_connections_generation.py``."""

import hashlib
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from classbeat.db import RETENTION, SYSTEM_TABLES
from classbeat.jobs.kinds import KIND_NAME, SONG_KIND_NAMES
from classbeat.operations.kinds import KIND_NAMES, SPOTIFY_WRITE_KINDS
from dbtools import alembic_config, migrated
from jobtools import as_user, engine_for, new_class, new_job, new_user
from queue_guard import check_queue
from retention_guard import check_retention
from rls_guard import check_tables
from role_guard import check_roles
from test_classes_schema import agent_run, count, insert, operation, revision, run_as

NEW_TABLES = ("spotify_oauth_flows", "spotify_pool", "class_playlists")
CREATE, REPLACE = "spotify_create_playlist", "spotify_replace_items"
PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"
OTHER_PLAYLIST = "0vvXsWCC9xrXsKd4FyS8kM"
CLIENT_ID = "0123456789abcdef0123456789abcdef"
VERIFIER = "A" * 43
CONNECTION_CATEGORIES = (
    "invalid_grant", "app_misconfigured", "not_allowlisted", "forbidden", "quota_exhausted",
    "not_found", "scope_mismatch", "invalid_response", "account_changed", "access_denied",
)  # fmt: skip
SONG_CATEGORIES = (
    "consent_missing", "pool_unusable", "pool_missing", "pool_not_synced", "playlist_mismatch",
    "pool_too_small",
)  # fmt: skip


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


def sha(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def flow(conn: Connection, owner: uuid.UUID, state: str = "s", session: str = "x", **c: Any):
    """A flow row; ``created_at`` and ``expires_at`` are SQL expressions (test constants)."""
    values: dict[str, Any] = {
        "owner_id": owner, "state_hash": sha(state), "session_hash": sha(session),
        "client_id": CLIENT_ID, "redirect_uri": "http://127.0.0.1:8000/oauth/spotify/callback",
        "verifier": VERIFIER, **c,
    }  # fmt: skip
    times = {"created_at": "now()", "expires_at": "now() + interval '10 minutes'"}
    times.update({k: values.pop(k) for k in list(times) if k in values})
    names = ", ".join([*values, *times])
    params = ", ".join([f":{k}" for k in values] + list(times.values()))
    return conn.execute(
        text(f"INSERT INTO spotify_oauth_flows ({names}) VALUES ({params}) RETURNING id"),  # noqa: S608
        values,
    ).scalar_one()


def pool(conn: Connection, owner: uuid.UUID, **c: Any) -> uuid.UUID:
    values: dict[str, Any] = {"spotify_user_id": "spotify-user-a", "consent_version": 1, **c}
    return insert(conn, "spotify_pool", owner_id=owner, **values)


def playlist(conn: Connection, owner: uuid.UUID, class_id: uuid.UUID, **c: Any) -> uuid.UUID:
    values: dict[str, Any] = {"playlist_id": PLAYLIST, "spotify_user_id": "spotify-user-a",
                              "name": "Tuesday climb", "state": "created", **c}  # fmt: skip
    return insert(conn, "class_playlists", owner_id=owner, class_id=class_id, **values)


def test_the_new_tables_meet_guards_1_2_9_and_10(admin: Connection) -> None:
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []
    assert check_queue(admin) == []
    assert check_retention(admin, RETENTION, SYSTEM_TABLES) == []
    for table in NEW_TABLES:
        policies = admin.execute(
            text("SELECT policyname, permissive FROM pg_policies WHERE tablename = :t"),
            {"t": table},
        ).all()
        assert [tuple(p) for p in policies] == [("owner_isolation", "PERMISSIVE")], table
        forced = admin.execute(
            text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).scalar_one()
        assert forced is True, table
    for table in NEW_TABLES:
        assert table in RETENTION
    assert "spotify_connection" in RETENTION["spotify_pool"].deleted_with
    assert "spotify_connection" in RETENTION["class_playlists"].deleted_with
    assert "spotify_connection" in RETENTION["external_operations"].deleted_with
    assert "spotify_connection" in RETENTION["connections"].deleted_with


def test_the_queue_functions_are_unchanged(admin: Connection) -> None:
    from classbeat.db.queue_check import QUEUE_VERSION

    assert QUEUE_VERSION == "classbeat-queue v2"
    assert check_queue(admin) == []


def test_two_users_share_nothing_in_the_new_tables(admin: Connection, app: Engine) -> None:
    a, b = new_user(admin), new_user(admin)
    class_a, class_b = new_class(admin, a), new_class(admin, b)
    rev_a = revision(admin, a, class_a)
    op_a = operation(admin, a, CREATE, class_id=class_a)
    flow(admin, a)
    pool_a = pool(admin, a, playlist_id=PLAYLIST)
    playlist(admin, a, class_a, revision_id=rev_a, create_operation_id=op_a)
    with app.begin() as connection:
        as_user(connection, b)
        for table in NEW_TABLES:
            assert count(connection, table) == 0, table
            assert connection.execute(text(f"UPDATE {table} SET owner_id = owner_id")).rowcount == 0  # noqa: S608
            assert connection.execute(text(f"DELETE FROM {table}")).rowcount == 0  # noqa: S608
        # A's id named directly gives nothing either (the take of ADR 0009 section 4 included).
        assert (
            connection.execute(
                text("SELECT count(*) FROM spotify_pool WHERE id = :p"), {"p": pool_a}
            ).scalar_one()
            == 0
        )
        taken = connection.execute(
            text("UPDATE spotify_oauth_flows SET used_at = now() WHERE state_hash = :h "
                 "AND session_hash = :s AND used_at IS NULL AND expires_at > now() RETURNING id"),
            {"h": sha("s"), "s": sha("x")},
        ).all()  # fmt: skip
        assert taken == []
    for table in NEW_TABLES:
        assert count(admin, table) == 1, table
    # B inserting rows that claim A's owner_id: refused by the policy's WITH CHECK.
    for statement, params in (
        ("INSERT INTO spotify_pool (owner_id, spotify_user_id, consent_version) "
         "VALUES (:a, 'x', 1)", {}),
        ("INSERT INTO spotify_oauth_flows (owner_id, state_hash, session_hash, client_id, "
         "redirect_uri, verifier, expires_at) VALUES (:a, :h, :h, :c, 'u', :v, "
         "now() + interval '1 minute')", {"h": sha("b"), "c": CLIENT_ID, "v": VERIFIER}),
        ("INSERT INTO class_playlists (owner_id, class_id, playlist_id, spotify_user_id, name, "
         "state) VALUES (:a, :class_a, :p, 'x', 'n', 'created')",
         {"class_a": class_a, "p": OTHER_PLAYLIST}),
    ):  # fmt: skip
        with pytest.raises(DBAPIError):
            run_as(app, b, statement, {"a": a, **params})
    # B's own rows pointing at A's class, revision or operation: the owner-inclusive keys refuse.
    insert_b = (
        "INSERT INTO class_playlists (owner_id, class_id, playlist_id, spotify_user_id, name, "
        "state, revision_id, create_operation_id) VALUES (:b, :c, :p, 'x', 'n', 'created', :r, :o)"
    )
    for class_id, rev, op in ((class_a, None, None), (class_b, rev_a, None), (class_b, None, op_a)):
        with pytest.raises(IntegrityError):
            run_as(
                app, b, insert_b, {"b": b, "c": class_id, "p": OTHER_PLAYLIST, "r": rev, "o": op}
            )
    run_as(app, b, insert_b, {"b": b, "c": class_b, "p": PLAYLIST, "r": None, "o": None})
    assert count(admin, "class_playlists") == 2  # B's own, with the same playlist id as A's


def test_a_playlist_pointer_names_a_revision_of_the_same_class(admin: Connection) -> None:
    owner = new_user(admin)
    class_1, class_2 = new_class(admin, owner), new_class(admin, owner)
    rev_2 = revision(admin, owner, class_2)
    with pytest.raises(IntegrityError, match="class_playlists_revision_fkey"):
        playlist(admin, owner, class_1, revision_id=rev_2)


@pytest.mark.parametrize(
    ("table", "columns", "constraint"),
    [
        ("spotify_oauth_flows", {"state_hash": b"x"}, "spotify_oauth_flows_state_hash"),
        ("spotify_oauth_flows", {"session_hash": b"x"}, "spotify_oauth_flows_session_hash"),
        ("spotify_oauth_flows", {"client_id": CLIENT_ID.upper()}, "spotify_oauth_flows_client_id"),
        ("spotify_oauth_flows", {"client_id": "abc"}, "spotify_oauth_flows_client_id"),
        ("spotify_oauth_flows", {"redirect_uri": ""}, "spotify_oauth_flows_redirect_uri"),
        ("spotify_oauth_flows", {"verifier": "A" * 42}, "spotify_oauth_flows_verifier"),
        ("spotify_oauth_flows", {"verifier": "A" * 42 + "="}, "spotify_oauth_flows_verifier"),
        ("spotify_oauth_flows", {"expires_at": "now() + interval '11 minutes'"},
         "spotify_oauth_flows_lifetime"),
        ("spotify_oauth_flows", {"expires_at": "now() - interval '1 second'"},
         "spotify_oauth_flows_lifetime"),
        ("spotify_pool", {"spotify_user_id": ""}, "spotify_pool_spotify_user_id"),
        ("spotify_pool", {"spotify_user_id": "x" * 101}, "spotify_pool_spotify_user_id"),
        ("spotify_pool", {"spotify_user_id": "a\nb"}, "spotify_pool_spotify_user_id"),
        ("spotify_pool", {"playlist_id": "short"}, "spotify_pool_playlist_id"),
        ("spotify_pool", {"playlist_id": PLAYLIST + "/"}, "spotify_pool_playlist_id"),
        ("spotify_pool", {"consent_version": 0}, "spotify_pool_consent_version"),
        ("spotify_pool", {"playlist_id": PLAYLIST, "last_hash": b"x"}, "spotify_pool_last_hash"),
        ("spotify_pool", {"last_hash": bytes(32)}, "spotify_pool_written"),
        ("class_playlists", {"playlist_id": "../" + PLAYLIST[3:]}, "class_playlists_playlist_id"),
        ("class_playlists", {"name": ""}, "class_playlists_name"),
        ("class_playlists", {"name": "x" * 101}, "class_playlists_name"),
        ("class_playlists", {"state": "deleted"}, "class_playlists_state"),
        ("class_playlists", {"content_hash": b"x"}, "class_playlists_content_hash"),
        ("class_playlists", {"track_count": -1}, "class_playlists_track_count"),
        ("class_playlists", {"track_count": 1001}, "class_playlists_track_count"),
        ("class_playlists", {"mowl_playlist_id": 0}, "class_playlists_mowl_playlist_id"),
        ("class_playlists", {"state": "verified"}, "class_playlists_verified"),
        ("class_playlists", {"spotify_user_id": ""}, "class_playlists_spotify_user_id"),
    ],
)  # fmt: skip
def test_new_table_checks(
    admin: Connection, table: str, columns: dict[str, Any], constraint: str
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    if table == "spotify_oauth_flows":
        make: Any = lambda: flow(admin, owner, **columns)  # noqa: E731
    elif table == "spotify_pool":
        make = lambda: pool(admin, owner, **columns)  # noqa: E731
    else:
        make = lambda: playlist(admin, owner, class_id, **columns)  # noqa: E731
    with pytest.raises(IntegrityError, match=constraint):
        make()


def test_the_valid_shapes_are_accepted(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    flow(admin, owner, verifier="aZ09_-" * 7 + "x", confirm_account_change=True)
    pool(admin, owner, playlist_id=PLAYLIST, last_hash=bytes(32), last_written_at="2026-10-30")
    playlist(admin, owner, class_id, state="verified", content_hash=bytes(32), track_count=0,
             mowl_playlist_id=7)  # fmt: skip


def test_one_pool_per_user_and_one_live_class_playlist_per_playlist(admin: Connection) -> None:
    a, b = new_user(admin), new_user(admin)
    pool(admin, a)
    with pytest.raises(IntegrityError, match="spotify_pool_owner_id_key"):
        pool(admin, a)
    pool(admin, b)  # another user's consent is no conflict
    class_a, class_a2, class_b = new_class(admin, a), new_class(admin, a), new_class(admin, b)
    first = playlist(admin, a, class_a)
    with pytest.raises(IntegrityError, match="class_playlists_live_playlist"):
        playlist(admin, a, class_a2)
    playlist(admin, b, class_b)  # the same id in another user's rows
    admin.execute(
        text("UPDATE class_playlists SET detached_at = now() WHERE id = :i"), {"i": first}
    )
    playlist(admin, a, class_a2)
    playlist(admin, a, class_a, detached_at="2026-01-01")  # history may repeat an id
    index = admin.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'class_playlists_live_playlist'")
    ).scalar_one()
    assert "UNIQUE" in index
    assert "(owner_id, playlist_id) WHERE (detached_at IS NULL)" in index


def test_a_flow_state_is_unique_per_owner(admin: Connection) -> None:
    a, b = new_user(admin), new_user(admin)
    flow(admin, a, "s1")
    with pytest.raises(IntegrityError, match="spotify_oauth_flows_owner_id_state_hash_key"):
        flow(admin, a, "s1", session="other")
    flow(admin, b, "s1")


def test_cascades_on_class_and_account_deletion_and_set_null(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    class_1, class_2 = new_class(admin, owner), new_class(admin, owner)
    rev = revision(admin, owner, class_1)
    op_final = operation(admin, owner, CREATE, class_id=class_1)
    kept = playlist(admin, owner, class_2, playlist_id=OTHER_PLAYLIST, revision_id=None)
    pointed = playlist(admin, owner, class_1, revision_id=rev, create_operation_id=op_final)
    op_pool = operation(admin, owner, CREATE)  # a pool row has no class
    flow(admin, owner)
    pool(admin, owner)
    # Deleting a revision or an operation only clears the pointer.
    with app.begin() as connection:
        as_user(connection, owner)
        connection.execute(text("DELETE FROM class_revisions WHERE id = :r"), {"r": rev})
        connection.execute(text("DELETE FROM external_operations WHERE id = :o"), {"o": op_final})
    row = admin.execute(
        text("SELECT revision_id, create_operation_id FROM class_playlists WHERE id = :i"),
        {"i": pointed},
    ).one()
    assert tuple(row) == (None, None)
    with app.begin() as connection:  # the class goes: its playlists with it, nothing else
        as_user(connection, owner)
        connection.execute(text("DELETE FROM classes WHERE id = :c"), {"c": class_1})
    ids = admin.execute(text("SELECT id FROM class_playlists")).scalars().all()
    assert ids == [kept]
    assert admin.execute(text("SELECT id FROM external_operations")).scalars().all() == [op_pool]
    assert (count(admin, "spotify_pool"), count(admin, "spotify_oauth_flows")) == (1, 1)
    with app.begin() as connection:  # the account goes: all
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    for table in (*NEW_TABLES, "external_operations"):
        assert count(admin, table) == 0, table


def test_external_operations_gain_the_spotify_kinds_and_every_category(admin: Connection) -> None:
    assert frozenset({CREATE, REPLACE}) == SPOTIFY_WRITE_KINDS <= KIND_NAMES
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    for kind in (CREATE, REPLACE):
        operation(admin, owner, kind)  # pool: no class
        operation(admin, owner, kind, class_id=class_id)  # final: the class
    with pytest.raises(IntegrityError, match="external_operations_kind"):
        operation(admin, owner, "spotify_delete_playlist")
    op = operation(admin, owner, REPLACE)
    for category in (*CONNECTION_CATEGORIES, *SONG_CATEGORIES):
        job_id = new_job(admin, owner, state="failed", error_category=category,
                         finished_at="2026-01-01")  # fmt: skip
        admin.execute(
            text("UPDATE external_operations SET state = 'failed', error_category = :c "
                 "WHERE id = :i"), {"c": category, "i": op},
        )  # fmt: skip
        agent_run(admin, owner, job_id, outcome=category, finished_at="2026-01-01")
    for category in ("song_picking_off", "pool_busy", "Invalid_Grant"):
        with pytest.raises(IntegrityError, match="jobs_error_category"):
            new_job(admin, owner, state="failed", error_category=category,
                    finished_at="2026-01-01")  # fmt: skip


def test_connections_gain_generation_and_the_spotify_last_errors(admin: Connection) -> None:
    owner = new_user(admin)
    insert_connection = text(
        "INSERT INTO connections (owner_id, provider, status, last_error, secret, secret_nonce, "
        "wrapped_dek, dek_nonce, key_id) VALUES (:o, 'spotify', 'needs_reconnect', :e, '\\x00', "
        ":n, :w, :n, 'kek-1') RETURNING generation"
    )
    params = {"o": owner, "n": bytes(12), "w": bytes(48)}
    for category in CONNECTION_CATEGORIES:
        generation = admin.execute(insert_connection, {**params, "e": category}).scalar_one()
        assert generation == 1
        admin.execute(text("DELETE FROM connections WHERE owner_id = :o"), {"o": owner})
    for category in SONG_CATEGORIES:  # job categories, never a connection's
        with pytest.raises(IntegrityError, match="connections_last_error"):
            admin.execute(insert_connection, {**params, "e": category})
    admin.execute(insert_connection, {**params, "e": None})
    with pytest.raises(IntegrityError, match="connections_generation"):
        admin.execute(text("UPDATE connections SET generation = 0 WHERE owner_id = :o"),
                      {"o": owner})  # fmt: skip


def test_the_song_job_kinds_are_reserved_names(admin: Connection) -> None:
    assert SONG_KIND_NAMES == ("design_with_songs", "revise_songs")
    owner = new_user(admin)
    for kind in SONG_KIND_NAMES:
        assert KIND_NAME.match(kind)
        job_id = new_job(admin, owner, kind=kind)
        agent_run(admin, owner, job_id, job_kind=kind)


def _take(engine: Engine, owner: uuid.UUID, state: str, session: str) -> list[tuple[Any, ...]]:
    """ADR 0009 section 4's take, as the app role under ``owner``'s row security."""
    with engine.begin() as connection:
        as_user(connection, owner)
        rows = connection.execute(
            text(
                "UPDATE spotify_oauth_flows SET used_at = now() WHERE state_hash = :h "
                "AND owner_id = :u AND session_hash = :s AND used_at IS NULL "
                "AND expires_at > now() RETURNING client_id, redirect_uri, verifier, "
                "confirm_account_change"
            ),
            {"h": sha(state), "u": owner, "s": sha(session)},
        )
        return [tuple(row) for row in rows]  # fmt: skip


def test_a_flow_is_taken_once_under_two_concurrent_takers(admin: Connection, app: Engine) -> None:
    owner, other = new_user(admin), new_user(admin)
    flow(admin, owner, "race", "sess")
    take = text(
        "UPDATE spotify_oauth_flows SET used_at = now() WHERE state_hash = :h AND owner_id = :u "
        "AND session_hash = :s AND used_at IS NULL AND expires_at > now() RETURNING verifier"
    )
    params = {"h": sha("race"), "u": owner, "s": sha("sess")}
    second: list[Any] = []
    with app.connect() as first:
        first.begin()
        as_user(first, owner)
        assert first.execute(take, params).scalars().all() == [VERIFIER]

        def other_taker() -> None:
            with app.begin() as connection:
                as_user(connection, owner)
                second.append(connection.execute(take, params).scalars().all())

        thread = threading.Thread(target=other_taker)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive()  # waits on the first taker's row lock
        first.commit()
    thread.join(10)
    assert second == [[]]  # re-checked after the lock: used, so nothing
    # Other bindings give nothing: another session, another user, an expired or used flow.
    flow(admin, owner, "s2", "sess")
    assert _take(app, owner, "s2", "another-session") == []
    assert _take(app, other, "s2", "sess") == []
    flow(admin, owner, "old", "sess", created_at="now() - interval '20 minutes'",
         expires_at="now() - interval '10 minutes'")  # fmt: skip
    assert _take(app, owner, "old", "sess") == []
    assert _take(app, owner, "race", "sess") == []
    assert [r[2] for r in _take(app, owner, "s2", "sess")] == [VERIFIER]


def _admin_engine(database_url: str) -> Engine:
    return create_engine(database_url, isolation_level="AUTOCOMMIT")


def test_upgrade_and_downgrade_on_an_empty_and_a_populated_database(
    owner_database_url: str, database_url: str
) -> None:
    config = alembic_config()
    admin_engine = _admin_engine(database_url)
    try:
        with migrated(owner_database_url, "0005_classes"):
            command.upgrade(config, "0006_spotify")  # empty
            command.downgrade(config, "0005_classes")
            with admin_engine.connect() as conn:  # an M3b database with data
                owner = new_user(conn)
                class_id = new_class(conn, owner)
                job_id = new_job(
                    conn, owner, class_id, state="failed", error_category="program_changed",
                    finished_at="2026-01-01",
                )  # fmt: skip
                operation(conn, owner, "mowl_save_workout", class_id=class_id)
                conn.execute(
                    text("INSERT INTO connections (owner_id, provider, status, secret, "
                         "secret_nonce, wrapped_dek, dek_nonce, key_id) VALUES (:o, 'mowl', "
                         "'connected', '\\x00', :n, :w, :n, 'kek-1')"),
                    {"o": owner, "n": bytes(12), "w": bytes(48)},
                )  # fmt: skip
            command.upgrade(config, "0006_spotify")
            with admin_engine.connect() as conn:
                assert conn.execute(text("SELECT generation FROM connections")).scalar_one() == 1
                assert (
                    conn.execute(
                        text("SELECT error_category FROM jobs WHERE id = :j"), {"j": job_id}
                    ).scalar_one()
                    == "program_changed"
                )
                # The new shapes, then a downgrade with them present.
                conn.execute(text("UPDATE connections SET generation = 3, last_error = "
                                  "'invalid_grant'"))  # fmt: skip
                pool(conn, owner, playlist_id=PLAYLIST)
                flow(conn, owner)
                spotify_op = operation(conn, owner, CREATE)
                playlist(conn, owner, class_id, create_operation_id=spotify_op)
                new_job(conn, owner, class_id, state="failed", error_category="pool_missing",
                        finished_at="2026-01-01", kind="design_with_songs")  # fmt: skip
                agent_run(conn, owner, job_id, outcome="playlist_mismatch",
                          finished_at="2026-01-01")  # fmt: skip
                conn.execute(
                    text("UPDATE external_operations SET state = 'failed', error_category = "
                         "'forbidden' WHERE kind = 'mowl_save_workout'")
                )  # fmt: skip
            command.downgrade(config, "0005_classes")
            with admin_engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT count(*) FROM pg_tables WHERE tablename = ANY(:t)"),
                    {"t": list(NEW_TABLES)},
                ).scalar_one()
                assert tables == 0
                kinds = conn.execute(text("SELECT kind FROM external_operations")).scalars().all()
                assert kinds == ["mowl_save_workout"]
                assert (
                    conn.execute(
                        text("SELECT error_category FROM external_operations")
                    ).scalar_one()
                    == "internal"
                )
                categories = conn.execute(
                    text("SELECT kind, error_category FROM jobs ORDER BY kind")
                ).all()
                assert [tuple(r) for r in categories] == [
                    ("design_with_songs", "internal"), ("test_kind", "program_changed"),
                ]  # fmt: skip
                outcome = conn.execute(text("SELECT outcome FROM agent_runs")).scalar_one()
                assert outcome == "internal"
                last_error = conn.execute(text("SELECT last_error FROM connections")).scalar_one()
                assert last_error == "unknown"
                columns = conn.execute(
                    text("SELECT column_name FROM information_schema.columns "
                         "WHERE table_name = 'connections' AND column_name = 'generation'")
                ).all()  # fmt: skip
                assert columns == []
                forced = (
                    conn.execute(
                        text("SELECT relforcerowsecurity FROM pg_class WHERE relname = ANY(:t)"),
                        {"t": ["agent_runs", "jobs", "external_operations", "connections"]},
                    )
                    .scalars()
                    .all()
                )
                assert forced == [True, True, True, True]
            command.upgrade(config, "head")  # and up again over the downgraded data
            with admin_engine.connect() as conn:
                assert check_tables(conn, SYSTEM_TABLES) == []
                assert check_retention(conn, RETENTION, SYSTEM_TABLES) == []
    finally:
        admin_engine.dispose()
