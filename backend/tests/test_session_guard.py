"""The fence guard of ADR 0005 section 3 (``session_scan``), and proof that it can fail."""

import ast
from pathlib import Path

import pytest

from session_scan import ALLOWED_MODULES, ALLOWED_PREFIXES, allowed, scan_source

SRC = Path(__file__).resolve().parent.parent / "src"


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_only_allowlisted_modules_open_a_user_session() -> None:
    violations = [
        (_module(path), line, why)
        for path in sorted((SRC / "classbeat").rglob("*.py"))
        for line, why in scan_source(path.read_text("utf-8"), _module(path))
    ]
    assert violations == []


def test_the_allowlist_is_exactly_the_reviewed_one() -> None:
    assert ALLOWED_PREFIXES == (
        "classbeat.connections",
        "classbeat.auth",
        "classbeat.db",
        "classbeat.usage",
    )
    assert ALLOWED_MODULES == (
        "classbeat.jobs.fence",
        "classbeat.jobs.credits",
        "classbeat.jobs.service",
        "classbeat.jobs.progress",
        "classbeat.operations.user",
        "classbeat.keys_cli",
    )


def importers(module: str) -> set[str]:
    """The source modules that import ``module`` (``import m``, ``from m import x``, ``from
    parent import name``)."""
    users = set()
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if module in names:
                users.add(_module(path))
    return users


def test_the_jobs_api_service_is_reached_only_from_its_routes() -> None:
    """``classbeat.jobs.service`` opens plain user sessions on jobs (the user's own writes); no
    worker, job-kind or core module may import it, only the web routes (J3, #87); and those
    routes only from the web app (#115 item 6)."""
    assert importers("classbeat.jobs.service") == {"classbeat.jobs.routes"}
    assert importers("classbeat.jobs.routes") == {"classbeat.web"}


def test_the_users_operation_actions_are_reached_only_from_their_routes() -> None:
    """``classbeat.operations.user`` opens plain user sessions (Check again, Do it anyway): only
    its web routes import it, and those only the web app (#117 item 3)."""
    assert importers("classbeat.operations.user") == {"classbeat.operations.routes"}
    assert importers("classbeat.operations.routes") == {"classbeat.web"}


@pytest.mark.parametrize(
    "module",
    [
        "classbeat.worker.loop",
        "classbeat.worker",
        "classbeat.agent.loop",
        "classbeat.web",
        "classbeat.jobsx",
        "classbeat.cli",
        "classbeat.dbtools",
        "classbeat.jobs",
        "classbeat.jobs.runner",
        "classbeat.jobs.run",
        "classbeat.jobs.design_draft",  # a job kind under classbeat.jobs gets no exemption
        "classbeat.jobs.fence.extra",
        "classbeat.keys_cli.sub",
        "classbeat.operations",
        "classbeat.operations.protocol",
        "classbeat.operations.attach",
        "classbeat.operations.routes",
        "classbeat.operations.user.extra",
    ],
)
def test_worker_agent_kind_and_other_code_is_not_allowlisted(module: str) -> None:
    assert not allowed(module)


@pytest.mark.parametrize(
    "module",
    [
        "classbeat.jobs.fence",
        "classbeat.jobs.credits",
        "classbeat.keys_cli",
        "classbeat.operations.user",
        "classbeat.connections.repository",
        "classbeat.auth.deps",
    ],
)
def test_the_owning_modules_are_allowlisted(module: str) -> None:
    assert allowed(module)


M = "classbeat.agent.loop"

ALLOWED = [
    "from classbeat.jobs import fenced_session\nasync with fenced_session(f, lease) as fs: pass",
    "from classbeat.db import system_session",
    "from classbeat.auth import revoke_user_sessions\nrevoke_user_sessions(db, uid)",
    '"""Writes go through fenced_session, never user_session or app.user_id."""',
    "def f():\n    '''A docstring may name user_session and app.user_id.'''",
    "x = 'user sessions'",
]
REFUSED = {
    "import": "from classbeat.db import user_session",
    "alias": "from classbeat.db import user_session as us",
    "module attribute": "import classbeat.db\nclassbeat.db.user_session(f, ctx)",
    "package attribute": "from classbeat import db\ndb.user_session(f, ctx)",
    "session module": "from classbeat.db.session import system_session",
    "session module import": "import classbeat.db.session",
    "session submodule": "from classbeat.db import session",
    "private helper": "from classbeat.db.session import _owned_session",
    "getattr string": "import classbeat.db as d\ngetattr(d, 'user_session')",
    "raw set_config": "q = \"SELECT set_config('app.user_id', :u, true)\"",
    "raw SET": "q = 'SET LOCAL app.user_id = x'",
}


@pytest.mark.parametrize("source", ALLOWED)
def test_allowed_sources(source: str) -> None:
    assert scan_source(source, M) == []


@pytest.mark.parametrize("source", REFUSED.values(), ids=REFUSED.keys())
def test_refused_sources(source: str) -> None:
    assert scan_source(source, M) != []


def test_an_allowlisted_module_may_use_it() -> None:
    assert scan_source(REFUSED["import"], "classbeat.jobs.fence") == []
