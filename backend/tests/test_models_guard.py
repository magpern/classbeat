"""The writer guard of the enable gate (ADR 0012 section 5 (a); #211), and proof that it fails on a
planted second writer. Static, no database."""

from pathlib import Path

import pytest

from results_scan import (
    TABLE_MODULES,
    TABLE_NAME_MODULES,
    WRITER_MODULES,
    scan_source,
)

SRC = Path(__file__).resolve().parent.parent / "src"


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_only_the_store_names_the_results_table_and_only_the_live_command_imports_the_writer() -> (
    None
):
    violations = [
        (_module(path), line, why)
        for path in sorted((SRC / "classbeat").rglob("*.py"))
        for line, why in scan_source(
            path.read_text("utf-8"), _module(path), is_package=path.name == "__init__.py"
        )
    ]
    assert violations == []


def test_the_allowlists_are_exactly_the_reviewed_ones() -> None:
    assert TABLE_MODULES == ("classbeat.models.store",)
    assert TABLE_NAME_MODULES == ("classbeat.db.tables",)
    assert WRITER_MODULES == ("classbeat.models.store", "classbeat.evals.suite_live")


def test_the_store_really_writes_the_table_and_defines_the_writer() -> None:
    """The guard checks something: the allowed module does both things it allows."""
    source = (SRC / "classbeat" / "models" / "store.py").read_text("utf-8")
    assert "INSERT INTO model_eval_results" in source
    assert "async def record_result(" in source
    assert scan_source(source, "classbeat.models.store") == []
    assert scan_source(source, "classbeat.models.routes") != []  # elsewhere it would fail


PLANTED_WRITERS = {
    "raw SQL": 'SQL = "INSERT INTO model_eval_results (id) VALUES (:id)"\n',
    "a table object": 'from sqlalchemy import table\nRESULTS = table("model_eval_results")\n',
    "an f-string": 'name = "x"\nSQL = f"UPDATE model_eval_results SET passed = {name}"\n',
    "an attribute": "def f(m):\n    return m.model_eval_results\n",
    "the writer imported": "from classbeat.models.store import record_result\n",
    "the writer imported under another name": (
        "from classbeat.models.store import record_result as save\n"
    ),
    "the writer through its module": (
        "from classbeat.models import store\n\nasync def f(db):\n"
        "    await store.record_result(db)\n"
    ),
    "the writer by getattr": (
        'from classbeat.models import store\nf = getattr(store, "record_result")\n'
    ),
    "the writer through the package": (
        "from classbeat import models\n\nasync def f(db):\n"
        "    await models.store.record_result(db)\n"
    ),
    "every name of the store": "from classbeat.models.store import *\n",
    "the writer by its full path": (
        "import classbeat.models.store\n\nasync def f(db):\n"
        "    await classbeat.models.store.record_result(db)\n"
    ),
}


@pytest.mark.parametrize("label", sorted(PLANTED_WRITERS))
def test_a_planted_second_writer_fails_the_guard(label: str) -> None:
    for module in ("classbeat.models.routes", "classbeat.evals.live", "classbeat.web"):
        assert scan_source(PLANTED_WRITERS[label], module) != [], (label, module)


def test_relative_imports_resolve_in_a_package_and_in_a_module() -> None:
    writer = "from .store import record_result\n"
    via_store = "from . import store\n\nasync def f(db):\n    await store.record_result(db)\n"
    for planted in (writer, via_store):
        assert scan_source(planted, "classbeat.models", is_package=True) != []
        assert scan_source(planted, "classbeat.models.cli") != []
        # In another package the same lines name another module: not the store.
        assert scan_source(planted, "classbeat.evals.live") == []
    assert scan_source("from ..models.store import record_result\n", "classbeat.evals.x") != []


def test_the_connections_record_result_is_not_the_writer() -> None:
    source = (
        "from classbeat import connections\nfrom classbeat.connections import record_result\n\n"
        "async def f(factory, ctx, opened):\n"
        "    await connections.record_result(factory, ctx, opened, None)\n"
        "    await record_result(factory, ctx, opened, None)\n"
    )
    assert scan_source(source, "classbeat.spotify.access") == []


def test_the_live_command_may_import_the_writer_but_not_name_the_table() -> None:
    allowed = "from classbeat.models.store import record_result\n"
    assert scan_source(allowed, "classbeat.evals.suite_live") == []
    assert scan_source(PLANTED_WRITERS["raw SQL"], "classbeat.evals.suite_live") != []


def test_the_registry_may_hold_the_bare_name_only() -> None:
    assert scan_source('T = {"model_eval_results": "reason"}\n', "classbeat.db.tables") == []
    assert scan_source('T = {"model_eval_results": "reason"}\n', "classbeat.db.retention") != []
    planted = 'SQL = "DELETE FROM model_eval_results"\n'
    assert scan_source(planted, "classbeat.db.tables") != []


def test_docstrings_and_comments_may_mention_both() -> None:
    source = (
        '"""Reads model_eval_results through record_result."""\n'
        "# model_eval_results, record_result\n"
        "def f():\n"
        '    """record_result writes model_eval_results."""\n'
    )
    assert scan_source(source, "classbeat.evals.gate") == []
