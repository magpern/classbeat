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
