"""Migration ``0009_models`` (ADR 0012 section 5; #211): the exact DDL, the NULL-proof CHECKs, the
composite foreign key, the app role's privileges, and every must-fail and must-succeed case of
PR #229 (73 and 8), run as ``classbeat_app``. ``10-roles.sql`` run again is in
``test_models_privileges.py``; ``record_result`` and the routes in ``test_models_routes.py``."""

import importlib.util
import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from classbeat.db import RETENTION, SYSTEM_TABLES
from jobtools import engine_for
from modeltools import clear_gate
from retention_guard import check_retention
from rls_guard import check_tables
from role_guard import check_roles

BACKEND = Path(__file__).resolve().parent.parent
ADR = BACKEND.parent / "docs" / "adr" / "0012-eval-suite-and-model-providers.md"
TABLES = ("model_eval_results", "model_routes")
A = "00000000-0000-0000-0000-00000000000a"  # a passing design result
B = "00000000-0000-0000-0000-00000000000b"  # a failing design result
C = "00000000-0000-0000-0000-00000000000c"  # a passing songs result


def models_migration() -> ModuleType:
    (path,) = (BACKEND / "migrations" / "versions").glob("*_models.py")
    spec = importlib.util.spec_from_file_location("models_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        clear_gate(connection)
        yield connection
        clear_gate(connection)


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


# --- the DDL is the ADR's, and the schema says what the ADR says ---------------------------------


def adr_ddl() -> str:
    body = ADR.read_text("utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", body, flags=re.DOTALL)
    (ddl,) = [b for b in blocks if "CREATE TABLE model_eval_results" in b]
    return ddl


def test_the_migration_issues_exactly_the_adrs_ddl_and_grants() -> None:
    migration = models_migration()
    assert adr_ddl() == migration.DDL
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON model_eval_results FROM classbeat_app" in (
        migration.DDL
    )
    assert "REVOKE TRUNCATE ON model_routes FROM classbeat_app" in migration.DDL
    # Besides it, only indexes and comments.
    assert all(s.startswith(("CREATE INDEX ", "COMMENT ON TABLE ")) for s in migration.EXTRAS)


def test_the_guards_hold_on_the_migrated_schema(admin: Connection) -> None:
    for table in TABLES:
        assert SYSTEM_TABLES[table].strip()
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []
    assert check_retention(admin, RETENTION, SYSTEM_TABLES) == []


def test_every_column_is_not_null_and_every_check_is_coalesced(admin: Connection) -> None:
    nullable = admin.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(:t) AND is_nullable = 'YES'"
        ),
        {"t": list(TABLES)},
    ).all()
    assert nullable == []
    checks = admin.execute(
        text(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE conrelid = ANY(CAST(:t AS regclass[])) AND contype = 'c'"
        ),
        {"t": [f"public.{t}" for t in TABLES]},
    ).all()
    assert len(checks) == 17  # 15 column checks, the verdict check, gate_passed
    for table, name, definition in checks:
        assert definition.startswith("CHECK (COALESCE("), (table, name)
        assert definition.endswith(", false))"), (table, name)


def test_the_foreign_key_is_match_full_restrict_and_nothing_is_deferrable(
    admin: Connection,
) -> None:
    rows = admin.execute(
        text(
            "SELECT conname, contype, condeferrable, confmatchtype, confupdtype, confdeltype "
            "FROM pg_constraint WHERE conrelid = ANY(CAST(:t AS regclass[]))"
        ),
        {"t": [f"public.{t}" for t in TABLES]},
    ).all()
    assert [r for r in rows if r.condeferrable] == []
    (fk,) = [r for r in rows if r.contype == "f"]
    assert (fk.conname, fk.confmatchtype, fk.confupdtype, fk.confdeltype) == (
        "model_routes_passing_result",
        "f",
        "r",
        "r",
    )


def test_the_app_role_may_only_read_and_insert_results(admin: Connection) -> None:
    def privileges(table: str) -> set[str]:
        return {
            p
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
            if admin.execute(
                text("SELECT has_table_privilege('classbeat_app', :t, :p)"),
                {"t": f"public.{table}", "p": p},
            ).scalar_one()
        }

    assert privileges("model_eval_results") == {"SELECT", "INSERT"}
    assert privileges("model_routes") == {"SELECT", "INSERT", "UPDATE", "DELETE"}


GOOD_RESULT: dict[str, Any] = {
    "id": "00000000-0000-0000-0000-000000000001",
    "run_id": "00000000-0000-0000-0000-000000000002",
    "recorded_at": "2026-10-31T00:00:00Z",
    "suite_version": 1,
    "manifest_digest": "0" * 64,
    "provider": "openai",
    "model": "gpt-x",
    "effort": "medium",
    "stage_group": "design",
    "prompt_versions": {},
    "classbeat_version": "0.3.0",
    "results_sha256": "0" * 64,
    "verdict": {"passed": True},
    "passed": True,
    "total_cost_micros": 0,
    "authorization_note": "owner test",
}
GOOD_ROUTE: dict[str, Any] = {
    "route_key": "r1",
    "stage_group": "design",
    "provider": "openai",
    "model": "gpt-x",
    "effort": "medium",
    "eval_result_id": "00000000-0000-0000-0000-000000000001",
    "gate_passed": True,
    "set_at": "2026-10-31T00:00:00Z",
    "set_by": "cli",
}


def test_every_check_refuses_null_by_itself(admin: Connection) -> None:
    """With NOT NULL taken away (a temp copy of each table and its CHECKs), each checked column
    set to NULL is still refused by its CHECK: none passes by evaluating to NULL."""

    # ``seq`` (migration 0010) is an identity column the copy does not carry: the row names it.
    results = {**GOOD_RESULT, "seq": 1}
    for table, good in (("model_eval_results", results), ("model_routes", GOOD_ROUTE)):
        probe = f"probe_{table}"
        admin.execute(text(f"CREATE TEMP TABLE {probe} (LIKE {table} INCLUDING CONSTRAINTS)"))
        for column in good:
            admin.execute(text(f"ALTER TABLE {probe} ALTER COLUMN {column} DROP NOT NULL"))
        checked = set(
            admin.execute(
                text(
                    "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a "
                    "ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                    "WHERE c.conrelid = CAST(:t AS regclass) AND c.contype = 'c'"
                ),
                {"t": probe},
            ).scalars()
        )
        insert = text(
            f"INSERT INTO {probe} SELECT * FROM jsonb_populate_record(NULL::{probe}, "  # noqa: S608
            "CAST(:row AS jsonb))"
        )
        admin.execute(insert, {"row": json.dumps(good)})  # the valid row goes in
        admin.execute(text(f"DELETE FROM {probe}"))  # noqa: S608
        refused = set()
        for column in sorted(checked):
            row = {"row": json.dumps({**good, column: None})}
            with pytest.raises(DBAPIError, match="check constraint"):  # autocommit: per row
                admin.execute(insert, row)
            refused.add(column)
            admin.execute(text(f"DELETE FROM {probe}"))  # noqa: S608
        assert refused == checked
        admin.execute(text(f"DROP TABLE {probe}"))
    # Every result column with a rule has a CHECK; the route's model, effort and result id are
    # held by the foreign key (and NOT NULL).
    assert checked == {"route_key", "stage_group", "provider", "gate_passed", "set_by"}


# --- PR #229's cases, as classbeat_app ---------------------------------------------------------

RESULT_COLUMNS = list(GOOD_RESULT)
ROUTE_COLUMNS = [c for c in GOOD_ROUTE if c != "set_at"]


def result_sql(**changes: Any) -> tuple[str, dict[str, Any]]:
    """An INSERT of one result with ``changes`` (``verdict`` and ``prompt_versions`` as JSON
    text, cast as the case files did; ``None`` is SQL NULL). Unchanged ids and runs are new."""
    values: dict[str, Any] = {
        **GOOD_RESULT,
        "id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "recorded_at": "now",
        "prompt_versions": "{}",
        "verdict": '{"passed": true}',
        **changes,
    }
    params = ", ".join(
        "now()"
        if c == "recorded_at" and values[c] == "now"
        else f"CAST(:{c} AS jsonb)"
        if c in ("verdict", "prompt_versions")
        else f":{c}"
        for c in RESULT_COLUMNS
    )
    sql = f"INSERT INTO model_eval_results ({', '.join(RESULT_COLUMNS)}) VALUES ({params})"  # noqa: S608
    return sql, {
        c: values[c] for c in RESULT_COLUMNS if not (c == "recorded_at" and values[c] == "now")
    }


def route_sql(**changes: Any) -> tuple[str, dict[str, Any]]:
    values = {**GOOD_ROUTE, "eval_result_id": A, **changes}
    params = ", ".join(f":{c}" for c in ROUTE_COLUMNS)
    sql = f"INSERT INTO model_routes ({', '.join(ROUTE_COLUMNS)}) VALUES ({params})"  # noqa: S608
    return sql, {c: values[c] for c in ROUTE_COLUMNS}


def raw(sql: str) -> tuple[str, dict[str, Any]]:
    return sql, {}


MUST_SUCCEED_RESULTS = {
    "passing design result": result_sql(id=A),
    "failing design result": result_sql(id=B, verdict='{"passed": false}', passed=False),
    "passing songs result": result_sql(id=C, stage_group="songs"),
    "verdict with extra keys and passed true": result_sql(
        verdict='{"passed": true, "groups": {"design": {"passed": true}}}'
    ),
    "verdict with duplicate passed keys, last true (jsonb keeps the last), passed true": (
        result_sql(verdict='{"passed": false, "passed": true}')
    ),
}
MUST_SUCCEED_ROUTES = {
    "route at the passing design result": route_sql(),
    "route at the passing songs result": route_sql(stage_group="songs", eval_result_id=C),
}
VERDICTS_REFUSED = {
    "verdict {}": "{}",
    'verdict {"x": 1}': '{"x": 1}',
    "verdict passed null": '{"passed": null}',
    "verdict passed string true": '{"passed": "true"}',
    "verdict passed number 1": '{"passed": 1}',
    "verdict passed object": '{"passed": {"v": true}}',
    "verdict passed array": '{"passed": [true]}',
    'verdict array ["passed"]': '["passed"]',
    'verdict string "passed"': '"passed"',
    "verdict number 1": "1",
    "verdict boolean true": "true",
    "verdict JSON null": "null",
    "verdict array of object": '[{"passed": true}]',
    "verdict nested object only": '{"result": {"passed": true}}',
    "verdict passed false but passed true": '{"passed": false}',
}
UPSERT_RESULT = result_sql(id=B)[0] + (
    " ON CONFLICT (id) DO UPDATE SET passed = true, verdict = '{\"passed\": true}'"
)
MUST_FAIL: dict[str, tuple[str, dict[str, Any]]] = {
    **{label: result_sql(verdict=v) for label, v in VERDICTS_REFUSED.items()},
    "verdict passed true but passed false": result_sql(passed=False),
    "duplicate keys, last true, but passed false": result_sql(
        verdict='{"passed": true, "passed": false}'
    ),
    "provider fake": result_sql(provider="fake"),
    "provider OpenAI": result_sql(provider="OpenAI"),
    "provider leading space": result_sql(provider=" openai"),
    "model trailing space": result_sql(model="gpt-x "),
    "model empty": result_sql(model=""),
    "effort unknown": result_sql(effort="ultra"),
    "stage_group unknown": result_sql(stage_group="all"),
    "prompt_versions array": result_sql(prompt_versions="[]"),
    "suite_version 0": result_sql(suite_version=0),
    "negative cost": result_sql(total_cost_micros=-1),
    "authorization_note empty": result_sql(authorization_note=""),
    "digest not hex": result_sql(manifest_digest="x"),
    **{f"result NULL {c}": result_sql(**{c: None}) for c in RESULT_COLUMNS},
    "UPDATE a failing result to a pass": raw(
        "UPDATE model_eval_results SET passed = true, verdict = '{\"passed\": true}' "  # noqa: S608
        f"WHERE id = '{B}'"
    ),
    "DELETE a failing result": raw(f"DELETE FROM model_eval_results WHERE id = '{B}'"),  # noqa: S608
    "TRUNCATE results": raw("TRUNCATE model_eval_results CASCADE"),
    "INSERT ... ON CONFLICT DO UPDATE on results (needs UPDATE)": (
        UPSERT_RESULT,
        result_sql(id=B)[1],
    ),
    "route at the failing result": route_sql(route_key="r2", eval_result_id=B),
    "route at the pass with another effort": route_sql(route_key="r2", effort="high"),
    "route at the pass with another model (case)": route_sql(route_key="r2", model="GPT-X"),
    "route at the pass with another provider": route_sql(route_key="r2", provider="anthropic"),
    "route at the design pass for songs": route_sql(route_key="r2", stage_group="songs"),
    "route gate_passed false at the failing result": route_sql(
        route_key="r2", eval_result_id=B, gate_passed=False
    ),
    "route at a random result id": route_sql(route_key="r2", eval_result_id=str(uuid.uuid4())),
    **{f"route NULL {c}": route_sql(**{"route_key": "r3", c: None}) for c in ROUTE_COLUMNS},
    "route upsert onto the failing result": (
        route_sql(eval_result_id=B)[0] + " ON CONFLICT (route_key, stage_group) "
        "DO UPDATE SET eval_result_id = EXCLUDED.eval_result_id",
        route_sql(eval_result_id=B)[1],
    ),
    "UPDATE route onto the failing result": raw(
        f"UPDATE model_routes SET eval_result_id = '{B}' "  # noqa: S608
        "WHERE route_key = 'r1' AND stage_group = 'design'"
    ),
    "UPDATE route effort": raw(
        "UPDATE model_routes SET effort = 'high' WHERE route_key = 'r1' AND stage_group = 'design'"
    ),
    "UPDATE route gate_passed to NULL": raw(
        "UPDATE model_routes SET gate_passed = NULL "
        "WHERE route_key = 'r1' AND stage_group = 'design'"
    ),
    "TRUNCATE routes": raw("TRUNCATE model_routes"),
    # SET CONSTRAINTS is accepted (``SETUP``); the insert after it is refused all the same.
    "deferred constraints then a bad route": route_sql(route_key="r4", eval_result_id=B),
    "session_replication_role replica": raw("SET session_replication_role = replica"),
    "ALTER TABLE drop the foreign key": raw(
        "ALTER TABLE model_routes DROP CONSTRAINT model_routes_passing_result"
    ),
    "ALTER TABLE disable triggers": raw("ALTER TABLE model_routes DISABLE TRIGGER ALL"),
}


SETUP = {"deferred constraints then a bad route": "SET CONSTRAINTS ALL DEFERRED"}
# What a refusal may be: not-null, foreign key, check, or no privilege (never a syntax error).
REFUSALS = {"23502", "23503", "23514", "42501"}


def test_the_case_list_is_pr_229s() -> None:
    assert len(MUST_FAIL) == 73
    assert len(MUST_SUCCEED_RESULTS) + len(MUST_SUCCEED_ROUTES) + 1 == 8  # + deleting a route
    assert len(RESULT_COLUMNS) == 16
    assert len(ROUTE_COLUMNS) == 8


@pytest.fixture
def seeded(admin: Connection, app: Engine) -> Engine:
    """The must-succeed rows, inserted as the app role: 5 results and 2 routes."""
    with app.begin() as conn:
        for sql, params in (*MUST_SUCCEED_RESULTS.values(), *MUST_SUCCEED_ROUTES.values()):
            conn.execute(text(sql), params)
    return app


def test_the_must_succeed_cases_go_through_as_the_app_role(
    seeded: Engine, admin: Connection
) -> None:
    rows = admin.execute(
        text("SELECT stage_group, passed FROM model_eval_results ORDER BY 1, 2")
    ).all()
    assert [tuple(r) for r in rows] == [
        ("design", False),
        ("design", True),
        ("design", True),
        ("design", True),
        ("songs", True),
    ]
    with seeded.begin() as conn:  # deleting a route disables a model
        deleted = conn.execute(
            text("DELETE FROM model_routes WHERE route_key = 'r1' AND stage_group = 'songs'")
        )
        assert deleted.rowcount == 1
    routes = admin.execute(text("SELECT route_key, stage_group FROM model_routes")).all()
    assert [tuple(r) for r in routes] == [("r1", "design")]


@pytest.mark.parametrize("label", sorted(MUST_FAIL))
def test_every_must_fail_case_is_refused_as_the_app_role(
    seeded: Engine, admin: Connection, label: str
) -> None:
    sql, params = MUST_FAIL[label]
    before = admin.execute(
        text(
            "SELECT (SELECT jsonb_agg(r ORDER BY id) FROM model_eval_results r), "
            "(SELECT jsonb_agg(m ORDER BY route_key, stage_group) FROM model_routes m)"
        )
    ).one()
    with seeded.connect() as conn:
        if label in SETUP:
            conn.execute(text(SETUP[label]))
        with pytest.raises(DBAPIError) as refused:  # at the statement: nothing is deferrable
            conn.execute(text(sql), params)
        assert getattr(refused.value.orig, "sqlstate", None) in REFUSALS, refused.value.orig
        conn.rollback()
        # The app role cannot have changed anything, nor its session settings.
        assert conn.execute(text("SHOW session_replication_role")).scalar_one() == "origin"
    after = admin.execute(
        text(
            "SELECT (SELECT jsonb_agg(r ORDER BY id) FROM model_eval_results r), "
            "(SELECT jsonb_agg(m ORDER BY route_key, stage_group) FROM model_routes m)"
        )
    ).one()
    assert tuple(after) == tuple(before)
