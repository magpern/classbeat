"""Guards 1 and 2 of ADR 0002 section 6, on the real schema, plus proof that they can fail."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from classbeat.db import SYSTEM_TABLES
from dbtools import CHILD, PARENT, drop_tables, owner_connection, run_rls_helper
from rls_guard import check_tables
from role_guard import check_roles

SCRATCH = ("t_bad", "t_sys")
GOOD = (
    "CREATE TABLE t_bad (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
    "owner_id uuid NOT NULL, name text, UNIQUE (owner_id, id){extra})"
)


def problems_of(owner_url: str, system: dict[str, str] | None = None) -> list[str]:
    with owner_connection(owner_url) as connection:
        return check_tables(connection, {**SYSTEM_TABLES, **(system or {})})


def test_the_migrated_schema_meets_the_contract(database_url: str) -> None:
    assert all(reason.strip() for reason in SYSTEM_TABLES.values())
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert check_tables(connection, SYSTEM_TABLES) == []
    finally:
        engine.dispose()


def test_the_test_tables_made_with_the_helper_meet_the_contract(
    owned_tables: None, owner_database_url: str
) -> None:
    assert problems_of(owner_database_url) == []


@pytest.fixture
def scratch(owned_tables: None, owner_database_url: str) -> Iterator[Connection]:
    """A transaction as the owner for building one deliberately wrong table."""
    drop_tables(owner_database_url, *SCRATCH)
    with owner_connection(owner_database_url) as connection:
        yield connection
    drop_tables(owner_database_url, *SCRATCH)


def violations(connection: Connection, system: dict[str, str] | None = None) -> list[str]:
    return check_tables(connection, {**SYSTEM_TABLES, **(system or {})})


def exec_all(connection: Connection, *statements: str) -> None:
    for statement in statements:
        connection.execute(text(statement))


def good(extra: str = "") -> str:
    return GOOD.format(extra=extra)


def test_an_exclusion_constraint_with_owner_id_passes(scratch: Connection) -> None:
    exec_all(scratch, good(", EXCLUDE USING btree (owner_id WITH =, name WITH =)"))
    run_rls_helper(scratch, "t_bad")
    assert violations(scratch) == []


def test_a_good_table_with_a_restrictive_extra_policy_and_set_null_columns_passes(
    scratch: Connection,
) -> None:
    exec_all(
        scratch,
        good(
            f", parent_id uuid, FOREIGN KEY (owner_id, parent_id) REFERENCES {PARENT} "
            "(owner_id, id) ON DELETE SET NULL (parent_id)"
        ),
    )
    run_rls_helper(scratch, "t_bad")
    exec_all(scratch, "CREATE POLICY narrow ON t_bad AS RESTRICTIVE USING (name <> 'x')")
    assert violations(scratch) == []


CASES = {
    "no rls": (
        [good()],
        "row level security is not enabled",
    ),
    "not forced": (
        [good(), "ALTER TABLE t_bad ENABLE ROW LEVEL SECURITY"],
        "row level security is not forced",
    ),
    "second permissive policy": (
        [good(), "RLS", "CREATE POLICY open ON t_bad USING (true)"],
        "expected exactly one permissive policy, found 2",
    ),
    "wrong expression": (
        [
            good(),
            "ALTER TABLE t_bad ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE t_bad FORCE ROW LEVEL SECURITY",
            "CREATE POLICY owner_isolation ON t_bad USING (true) WITH CHECK (true)",
        ],
        "wrong USING or WITH CHECK",
    ),
    "policy for one role": (
        [
            good(),
            "ALTER TABLE t_bad ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE t_bad FORCE ROW LEVEL SECURITY",
            "CREATE POLICY owner_isolation ON t_bad TO classbeat_app USING (true)",
        ],
        "FOR ALL TO PUBLIC",
    ),
    "nullable owner_id": (
        [
            "CREATE TABLE t_bad (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid, "
            "UNIQUE (owner_id, id))",
            "RLS",
        ],
        "owner_id must be uuid NOT NULL",
    ),
    "id is text": (
        [
            "CREATE TABLE t_bad (id text PRIMARY KEY, owner_id uuid NOT NULL, "
            "UNIQUE (owner_id, id))",
            "RLS",
        ],
        "id must be uuid with the server default",
    ),
    "id without a server default": (
        [
            "CREATE TABLE t_bad (id uuid PRIMARY KEY, owner_id uuid NOT NULL, "
            "UNIQUE (owner_id, id))",
            "RLS",
        ],
        "id must be uuid with the server default",
    ),
    "id with another default": (
        [
            "CREATE TABLE t_bad (id uuid PRIMARY KEY DEFAULT (md5(random()::text))::uuid, "
            "owner_id uuid NOT NULL, UNIQUE (owner_id, id))",
            "RLS",
        ],
        "id must be uuid with the server default",
    ),
    "exclusion constraint without owner_id": (
        [good(", EXCLUDE USING btree (name WITH =)"), "RLS"],
        "must include owner_id WITH =",
    ),
    "no owner_id": (
        ["CREATE TABLE t_bad (id uuid PRIMARY KEY DEFAULT gen_random_uuid())"],
        "no owner_id column",
    ),
    "no unique owner_id, id": (
        [
            "CREATE TABLE t_bad (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "owner_id uuid NOT NULL)",
            "RLS",
        ],
        "missing UNIQUE (owner_id, id)",
    ),
    "global unique": (
        [good(", UNIQUE (name)"), "RLS"],
        "does not include owner_id",
    ),
    "global unique index": (
        [good(), "CREATE UNIQUE INDEX t_bad_name ON t_bad (lower(name))", "RLS"],
        "does not include owner_id",
    ),
    "unique without owner_id": (
        [good(), "CREATE UNIQUE INDEX t_bad_id ON t_bad (id, name)", "RLS"],
        "does not include owner_id",
    ),
    "partitioned": (
        [
            "CREATE TABLE t_bad (id uuid NOT NULL, owner_id uuid NOT NULL) "
            "PARTITION BY LIST (owner_id)",
            "RLS",
        ],
        "must not be partitioned",
    ),
    "foreign key without the owner": (
        [good(f", parent_id uuid REFERENCES {PARENT} (id)"), "RLS"],
        "must map owner_id to the parent's owner_id",
    ),
    "swapped foreign key": (
        [
            f"CREATE UNIQUE INDEX t_parent_swapped ON {PARENT} (id, owner_id)",
            good(
                f", parent_id uuid, FOREIGN KEY (owner_id, parent_id) REFERENCES {PARENT} "
                "(id, owner_id)"
            ),
            "RLS",
        ],
        "must map owner_id to the parent's owner_id",
    ),
    "bare set null": (
        [
            good(
                f", parent_id uuid, FOREIGN KEY (owner_id, parent_id) REFERENCES {PARENT} "
                "(owner_id, id) ON DELETE SET NULL"
            ),
            "RLS",
        ],
        "needs a column list without owner_id",
    ),
    "set null including owner_id": (
        [
            good(
                f", parent_id uuid, FOREIGN KEY (owner_id, parent_id) REFERENCES {PARENT} "
                "(owner_id, id) ON DELETE SET NULL (owner_id, parent_id)"
            ),
            "RLS",
        ],
        "needs a column list without owner_id",
    ),
}


@pytest.mark.parametrize(("statements", "expected"), CASES.values(), ids=CASES.keys())
def test_the_guard_fails_for_a_table_that_breaks_the_contract(
    scratch: Connection, statements: list[str], expected: str
) -> None:
    for statement in statements:
        if statement == "RLS":
            run_rls_helper(scratch, "t_bad")
        else:
            scratch.execute(text(statement))
    found = violations(scratch)
    assert any(expected in problem for problem in found), found
    assert all(problem.startswith("t_bad:") for problem in found), found


def test_a_view_in_public_fails(scratch: Connection) -> None:
    exec_all(scratch, "CREATE VIEW t_view AS SELECT * FROM " + PARENT)  # noqa: S608
    try:
        assert violations(scratch) == ["t_view: relation kind 'v' is not allowed in public"]
    finally:
        exec_all(scratch, "DROP VIEW t_view")


def test_a_system_table_must_not_have_owner_id(scratch: Connection) -> None:
    exec_all(scratch, "CREATE TABLE t_sys (id uuid PRIMARY KEY, owner_id uuid)")
    found = violations(scratch, {"t_sys": "test"})
    assert found == ["t_sys: a system table must not have owner_id"]


def test_a_system_table_must_not_reference_an_owned_table(scratch: Connection) -> None:
    exec_all(
        scratch, f"CREATE TABLE t_sys (id uuid PRIMARY KEY, ref uuid REFERENCES {PARENT} (id))"
    )
    found = violations(scratch, {"t_sys": "test"})
    assert [p for p in found if "into owned table" in p], found


def test_a_system_table_needs_a_reason(scratch: Connection) -> None:
    exec_all(scratch, "CREATE TABLE t_sys (id uuid PRIMARY KEY)")
    assert violations(scratch, {"t_sys": " "}) == ["t_sys: SYSTEM_TABLES needs a reason"]


def test_an_unlisted_table_without_the_contract_fails(scratch: Connection) -> None:
    exec_all(scratch, "CREATE TABLE t_sys (id uuid PRIMARY KEY)")
    assert any("no owner_id column" in p for p in violations(scratch))


# Guard 2.


def test_roles_and_privileges_match_the_adr(database_url: str, owned_tables: None) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert check_roles(connection) == []
    finally:
        engine.dispose()


def admin_problems(database_url: str, *statements: str, undo: tuple[str, ...] = ()) -> list[str]:
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            try:
                exec_all(connection, *statements)
                return check_roles(connection)
            finally:
                exec_all(connection, *undo)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("statements", "undo", "expected"),
    [
        (
            [f"GRANT TRUNCATE ON {PARENT} TO classbeat_app"],
            [f"REVOKE TRUNCATE ON {PARENT} FROM classbeat_app"],
            "privileges must be exactly",
        ),
        (
            [f"GRANT REFERENCES, TRIGGER, MAINTAIN ON {PARENT} TO classbeat_app"],
            [f"REVOKE REFERENCES, TRIGGER, MAINTAIN ON {PARENT} FROM classbeat_app"],
            "privileges must be exactly",
        ),
        (
            [f"GRANT SELECT ON {CHILD} TO PUBLIC"],
            [f"REVOKE SELECT ON {CHILD} FROM PUBLIC"],  # noqa: S608
            "PUBLIC must have no privilege",
        ),
        (
            [f"GRANT SELECT (name) ON {PARENT} TO PUBLIC"],
            [f"REVOKE SELECT (name) ON {PARENT} FROM PUBLIC"],  # noqa: S608
            "column-level",
        ),
        (
            ["GRANT TEMP ON DATABASE {db} TO PUBLIC"],
            ["REVOKE TEMP ON DATABASE {db} FROM PUBLIC"],
            "PUBLIC must have no privilege on the database",
        ),
        (
            ["GRANT CREATE ON SCHEMA public TO classbeat_app"],
            ["REVOKE CREATE ON SCHEMA public FROM classbeat_app"],
            "USAGE only on public",
        ),
        (
            ["GRANT USAGE ON SCHEMA public TO PUBLIC"],
            ["REVOKE USAGE ON SCHEMA public FROM PUBLIC"],
            "PUBLIC must have no privilege on public",
        ),
        (
            ["ALTER ROLE classbeat_app BYPASSRLS"],
            ["ALTER ROLE classbeat_app NOBYPASSRLS"],
            "must not have rolbypassrls",
        ),
        (
            ["ALTER ROLE classbeat_owner BYPASSRLS"],
            ["ALTER ROLE classbeat_owner NOBYPASSRLS"],
            "must not have rolbypassrls",
        ),
        (
            ["CREATE ROLE guard_bypass NOLOGIN BYPASSRLS", "GRANT guard_bypass TO classbeat_owner"],
            ["DROP ROLE guard_bypass"],
            "must be a member of no role",
        ),
        (
            ["ALTER ROLE classbeat_app SET lock_timeout = '1h'"],
            ["ALTER ROLE classbeat_app SET lock_timeout = '5s'"],
            "role settings must be exactly",
        ),
        (
            ["ALTER ROLE classbeat_app IN DATABASE {db} SET statement_timeout = 0"],
            ["ALTER ROLE classbeat_app IN DATABASE {db} RESET ALL"],
            "none per database",
        ),
        (
            ["ALTER ROLE classbeat_app SET session_replication_role = replica"],
            ["ALTER ROLE classbeat_app RESET session_replication_role"],
            "role settings must be exactly",
        ),
        (
            ["ALTER ROLE classbeat_app SET search_path = pg_temp"],
            ["ALTER ROLE classbeat_app RESET search_path"],
            "role settings must be exactly",
        ),
        (
            ["GRANT CONNECT ON DATABASE postgres TO PUBLIC"],
            ["REVOKE CONNECT ON DATABASE postgres FROM PUBLIC"],
            "must not connect to",
        ),
        (
            [
                "ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner "
                "GRANT MAINTAIN ON TABLES TO classbeat_app"
            ],
            [
                "ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner "
                "REVOKE MAINTAIN ON TABLES FROM classbeat_app"
            ],
            "unexpected default privileges",
        ),
        (
            [
                "ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public "
                "GRANT TRUNCATE ON TABLES TO classbeat_app"
            ],
            [
                "ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner IN SCHEMA public "
                "REVOKE TRUNCATE ON TABLES FROM classbeat_app"
            ],
            "default privileges on r in public",
        ),
        (
            [
                "ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner "
                "GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
            ],
            [
                "ALTER DEFAULT PRIVILEGES FOR ROLE classbeat_owner "
                "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            ],
            "default privileges for functions must be set",
        ),
    ],
)
def test_the_role_guard_fails_when_a_privilege_drifts(
    database_url: str,
    owned_tables: None,
    statements: list[str],
    undo: list[str],
    expected: str,
) -> None:
    database = create_engine(database_url)
    with database.connect() as connection:
        db = connection.execute(text("SELECT current_database()")).scalar_one()
    database.dispose()
    found = admin_problems(
        database_url,
        *[s.replace("{db}", db) for s in statements],
        undo=tuple(u.replace("{db}", db) for u in undo),
    )
    assert any(expected in problem for problem in found), found
    assert admin_problems(database_url) == [], "the undo statements must restore the state"


def test_a_function_made_by_the_owner_is_not_callable_by_public_or_the_app(
    database_url: str, owner_database_url: str, app_database_url: str
) -> None:
    with owner_connection(owner_database_url) as connection:
        exec_all(connection, "CREATE FUNCTION t_fn() RETURNS int LANGUAGE sql AS 'SELECT 1'")
    try:
        assert admin_problems(database_url) == []
        engine = create_engine(app_database_url)
        try:
            with (
                engine.connect() as connection,
                pytest.raises(Exception, match="permission denied"),
            ):
                connection.execute(text("SELECT public.t_fn()"))
        finally:
            engine.dispose()
    finally:
        with owner_connection(owner_database_url) as connection:
            exec_all(connection, "DROP FUNCTION t_fn()")


def test_public_cannot_connect_to_the_database(database_url: str) -> None:
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    probe = engine.url.set(username="guard_probe", password="probe-password")
    try:
        with engine.connect() as connection:
            exec_all(connection, "CREATE ROLE guard_probe LOGIN PASSWORD 'probe-password'")
        try:
            with pytest.raises(OperationalError, match="permission denied for database"):
                create_engine(probe).connect()
        finally:
            with engine.connect() as connection:
                exec_all(connection, "DROP ROLE guard_probe")
    finally:
        engine.dispose()


def test_inheritance_leaks_rows_across_users_and_the_guard_refuses_it(
    owner_database_url: str, app_database_url: str, owned_tables: None
) -> None:
    """The review's case: an owned child inherits a system table; A reads B through the parent."""
    user_a, user_b = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
    drop_tables(owner_database_url, "t_bad", "t_sys")
    with owner_connection(owner_database_url) as connection:
        exec_all(connection, "CREATE TABLE t_sys (name text)", good() + " INHERITS (t_sys)")
        run_rls_helper(connection, "t_bad")
    app = create_engine(app_database_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": user_b})
            connection.execute(
                text("INSERT INTO t_bad (owner_id, name) VALUES (:o, 'B-secret')"), {"o": user_b}
            )
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": user_a})
            own = connection.execute(text("SELECT name FROM t_bad")).scalars().all()
            via_parent = connection.execute(text("SELECT name FROM t_sys")).scalars().all()
        assert own == []
        assert via_parent == ["B-secret"], "the hole: Postgres applies only the parent's policy"
        found = problems_of(owner_database_url, {"t_sys": "test"})
        assert any("inheritance hierarchy" in p for p in found), found
    finally:
        app.dispose()
        drop_tables(owner_database_url, "t_bad", "t_sys")
