"""Guard 8 of ADR 0003 section 1, and proof that it can fail."""

from pathlib import Path

import pytest

from auth_tables_scan import scan_source

SRC = Path(__file__).resolve().parent.parent / "src"


def test_nothing_outside_classbeat_auth_touches_the_account_tables() -> None:
    violations = []
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("classbeat/auth/"):
            continue
        violations += [(relative, line, why) for line, why in scan_source(path.read_text("utf-8"))]
    assert violations == []


def test_the_scan_covers_the_auth_package_itself_in_one_place_only() -> None:
    inside = [p for p in (SRC / "classbeat" / "auth").rglob("*.py") if p.name == "tables.py"]
    assert len(inside) == 1  # the one module the guard protects


ALLOWED = [
    "from classbeat import auth\nauth.create_invite",
    "from classbeat.auth import purge_expired, create_invite, SessionInfo",
    "import classbeat.auth as auth\nauth.purge_expired",
    "from classbeat import auth\nauth.require_user\ngetattr(auth, 'purge_expired')",
    "from sqlalchemy import table\ntable('other')",
    "import importlib\nimportlib.import_module('classbeat.context')",
    "from . import auth",
    "from .auth import purge_expired",
    "x = 'select * from somewhere_else'",
    "x = 'the users of sessions are listed'",  # words, no SQL keyword before them
    "def f(users, sessions): return users",
    "from classbeat.db.tables import SYSTEM_TABLES",
    "s = 'SELECT 1 FROM alembic_version'",
    "x = 'purge of expired sessions finished'",  # log messages and docstrings: no SQL verb
    "x = 'all users are listed in the account page'",
    "import classbeat\nclassbeat.auth.create_invite",
    "import classbeat as c\nc.auth.purge_expired\nc.cli.run_command",
    "from .. import classbeat\nclassbeat.auth.require_user",
    "from sqlalchemy import table\ntable('other', name='users_view_cache_x')",
    "import importlib\nimportlib.import_module('classbeat.auth').purge_expired",
    "import pkgutil\npkgutil.resolve_name('classbeat.context:UserContext')",
    "x = vars(object())",
    "x = 'files copied from sessions of the old host'",  # no statement verb
    "x = 'Rows are copied from the sessions of the user into a report'",  # no adjacency
    "x = 'update the sessions after a select of users'",  # keyword not followed by a table
    "x = 'select the data copied from sessions_archive'",  # a different name
    "x = 'delete the invites of the group'",
    "x = 'copied from sessions'",
    "x = 'copied from sessions'",
    "x = f'{n} rows copied from sessions of {u}'",
    "x = 'rows ' + 'copied from sessions'",
    "x = 'Showing ' + str(n) + ' users'",
    "x = 1 + 2",
    "x = 'the table sessions is described in the docs'",
    "__import__('classbeat.context').context",
    "__import__('classbeat.auth').auth.create_invite",
]
REFUSED = {
    "import module": "import classbeat.auth.tables",
    "from import": "from classbeat.auth.tables import users",
    "from submodule import": "from classbeat.auth.tables import sessions as s",
    "package import": "from classbeat.auth import tables",
    "submodule import": "from classbeat.auth.sessions import SessionInfo",
    "submodule table": "from classbeat.auth.sessions import sessions, users",
    "submodule alias": "import classbeat.auth.purge as p\np.sessions",
    "import submodule": "import classbeat.auth.keys",
    "table name from package": "from classbeat.auth import sessions",
    "metadata from package": "from classbeat.auth import metadata",
    "relative submodule": "from .auth.sessions import SessionInfo",
    "relative package submodule": "from .auth import tables",
    "getattr submodule": "from classbeat import auth\ngetattr(auth, 'tables')",
    "getattr table": "import classbeat.auth as a\ngetattr(a, 'users')",
    "table attribute on alias": "from classbeat import auth\nauth.sessions",
    "metadata attribute": "import classbeat.auth\nclassbeat.auth.metadata",
    "import_module submodule": (
        "from importlib import import_module\nimport_module('classbeat.auth.sessions')"
    ),
    "relative import_module": (
        "import importlib\nimportlib.import_module('.sessions', 'classbeat.auth')"
    ),
    "__import__": "__import__('classbeat.auth.tables')",
    "sqlalchemy table": "from sqlalchemy import table\ntable('sessions')",
    "sqlalchemy Table": "import sqlalchemy as sa\nsa.Table('users', sa.MetaData())",
    "star import": "from classbeat.auth import *",
    "classbeat alias": "import classbeat as c\nc.auth.tables",
    "classbeat alias table": "import classbeat as c\nc.auth.sessions",
    "classbeat package": "import classbeat\nclassbeat.auth.sessions",
    "classbeat relative root": "from .. import classbeat\nclassbeat.auth.users",
    "import_module attribute": (
        "import importlib\nimportlib.import_module('classbeat.auth').tables"
    ),
    "import_module assigned": (
        "import importlib\na = importlib.import_module('classbeat.auth')\na.users"
    ),
    "__import__ attribute": "__import__('classbeat.auth').metadata",
    "getattr on import_module": (
        "import importlib\ngetattr(importlib.import_module('classbeat.auth'), 'tables')"
    ),
    "getattr on classbeat alias": "import classbeat as c\ngetattr(c.auth, 'users')",
    "table keyword": "from sqlalchemy import table\ntable(name='users')",
    "Table keyword": "import sqlalchemy as sa\nsa.Table(name='sessions', metadata=m)",
    "vars": "from classbeat import auth\nvars(auth)",
    "__dict__": "from classbeat import auth\nauth.__dict__",
    "__dict__ via classbeat": "import classbeat as c\nc.auth.__dict__",
    "resolve_name": "import pkgutil\npkgutil.resolve_name('classbeat.auth:tables')",
    "comma join": "q = 'SELECT 1 FROM a, users'",
    "quoted schema": 'q = \'select 1 from "public"."users"\'',
    "using": "q = 'DELETE FROM other USING sessions WHERE other.id = sessions.id'",
    "copy": "q = 'COPY users TO STDOUT'",
    "parentheses": "q = 'select * from (select id from invites) i'",
    "comments": "q = '-- note\\nSELECT 1 /* c */ FROM /* c */ auth_tokens'",
    "alter": "q = 'ALTER TABLE mfa_totp ADD COLUMN x int'",
    "merge": "q = 'MERGE INTO known_devices USING t ON true'",
    "with cte": "q = 'WITH x AS (SELECT 1) SELECT * FROM oauth_flows'",
    "f-string": "q = f'SELECT * FROM users WHERE id = {i}'",
    "text keyword": "q = text(statement='select 1 from identities')",
    "schema": "q = 'select 1 from public.pending_signups'",
    "attribute": "import classbeat.auth\nclassbeat.auth.tables.users",
    "alias attribute": "from classbeat import auth\nauth.tables.sessions",
    "as alias attribute": "import classbeat.auth as a\na.tables.users",
    "importlib": "import importlib\nimportlib.import_module('classbeat.auth.tables')",
    "select": "q = text('SELECT * FROM users WHERE id = :i')",
    "join": "q = 'SELECT 1 FROM x JOIN sessions s ON s.user_id = x.id'",
    "insert": "q = 'INSERT INTO invites (code_hash) VALUES (1)'",
    "update": "q = 'UPDATE users SET role = 1'",
    "delete": "q = 'DELETE FROM rate_limits'",
    "truncate": "q = 'TRUNCATE auth_events'",
    "qualified": "q = 'SELECT * FROM public.mfa_totp'",
    "quoted": "q = 'SELECT * FROM \"known_devices\"'",
    "lower case": "q = 'select * from oauth_flows'",
    "multi-line": "q = '''\\nSELECT *\\n  FROM\\n  pending_signups'''",
    "lock": "q = 'LOCK TABLE auth_tokens'",
    "recovery": "q = 'delete from mfa_recovery_codes'",
    "identities": "q = 'select 1 from identities'",
    "__import__ then auth.tables": "__import__('classbeat.auth').auth.tables",
    "__import__ then auth.sessions": "__import__('classbeat.auth').auth.sessions",
    "__import__ assigned": "c = __import__('classbeat.auth')\nc.auth.users",
    "__import__ classbeat": "__import__('classbeat').auth.tables",
    "import_module classbeat": (
        "import importlib\nimportlib.import_module('classbeat').auth.metadata"
    ),
    "truncate without TABLE": "q = 'TRUNCATE users, sessions'",
    "only modifier": "q = 'DELETE FROM ONLY users'",
    "drop if exists": "q = 'DROP TABLE IF EXISTS invites'",
    "from list with alias": "q = 'select 1 from a x, b as y, auth_events'",
    "keyword then comment": "q = 'select 1 from /* c */ users'",
    # verb and table in different pieces (review of #55)
    "f-string column list": 'q = text(f"SELECT {cols} FROM users WHERE id = :id")',
    "f-string from list": 'q = f"SELECT {c} FROM a, users"',
    "f-string delete": 'q = f"DELETE {x} FROM users"',
    "plus chain from": 'q = "SELECT * " + "FROM users"',
    "plus chain into": 'q = "INSERT " + "INTO invites VALUES (1)"',
    "plus chain join": 'q = "SELECT * FROM a " + "JOIN sessions ON true"',
    "plus chain with a name": 'q = "SELECT " + cols + " FROM users"',
    "plus chain in a call": 'q = text("SELECT * " + "FROM " + "users")',
    "bare fragment": 'q = " FROM users WHERE id = :id"',
    "bare fragment start": 'q = "FROM sessions"',
    "bare join fragment": 'q = " JOIN identities i ON i.user_id = u.id"',
    "bare update fragment": 'q = "UPDATE users SET role = 1"',
    "bare fragment with alias": 'q = " FROM users u WHERE u.id = 1"',
    "bare fragment semicolon": 'q = "x FROM users;"',
    "fragment in an f-string": 'q = f" FROM users WHERE id = {i}"',
    # narrowed in the first version (review of #55)
    "grant on": "q = 'GRANT SELECT ON users TO x'",
    "revoke on": "q = 'REVOKE ALL ON sessions FROM x'",
    "grant on table": "q = 'GRANT ALL ON TABLE invites TO x'",
    "any schema": "q = 'select * from classbeat.users'",
    "any quoted schema": 'q = \'select * from "app"."users"\'',
    "quoted without space": "q = 'SELECT count(*) FROM\"users\"'",
    "join quoted without space": "q = 'select 1 from x join\"sessions\" on true'",
}


@pytest.mark.parametrize("source", ALLOWED)
def test_allowed(source: str) -> None:
    assert scan_source(source) == []


@pytest.mark.parametrize("source", REFUSED.values(), ids=REFUSED.keys())
def test_refused(source: str) -> None:
    assert scan_source(source) != []


def test_the_auth_package_does_not_re_export_a_table_object() -> None:
    from sqlalchemy import MetaData, Table

    import classbeat.auth as package
    from auth_tables_scan import TABLE_NAMES

    for name in dir(package):
        assert not isinstance(getattr(package, name), Table | MetaData), name
    assert not {"metadata", *TABLE_NAMES} & set(package.__all__)
