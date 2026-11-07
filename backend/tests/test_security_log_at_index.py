"""#750: ``security_log (at)`` exists, and the admin abuse view's window queries and the 7-day
purge plan on an index on ``at``."""

import json
from typing import Any

from alembic import command
from sqlalchemy import create_engine, text

from classbeat.admin import abuse
from classbeat.auth import securitylog
from dbtools import migrated
from jobtools import new_user
from test_migrations import alembic_config

INDEX = "security_log_at"
OWNER_INDEX = "security_log_owner_at"  # (owner_id, at), 0034: the per-user purge
WINDOW_QUERIES = {
    "totals": abuse._TOTALS,  # pyright: ignore[reportPrivateUsage]
    "top": abuse._TOP,  # pyright: ignore[reportPrivateUsage]
    "limited": abuse._LIMITED,  # pyright: ignore[reportPrivateUsage]
    "moved": abuse._MOVED,  # pyright: ignore[reportPrivateUsage]
}


def index_definitions(conn: Any) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'security_log' AND indexname = :i"
            ),
            {"i": INDEX},
        ).scalars()
    )


def plan_of(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = conn.execute(text("EXPLAIN (FORMAT JSON) " + sql), params).scalar_one()
    return (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]


def plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    found = [plan]
    for child in plan.get("Plans", []):
        found.extend(plan_nodes(child))
    return found


def test_index_exists_and_downgrade_drops_it(owner_database_url: str, database_url: str) -> None:
    # ``migrated`` starts from a clean schema and, whatever fails below, takes the schema back to
    # base afterwards (a failure between the downgrade and the second upgrade included).
    config = alembic_config()
    admin = create_engine(database_url)
    try:
        with migrated(owner_database_url):
            with admin.connect() as conn:
                assert index_definitions(conn) == [
                    "CREATE INDEX security_log_at ON public.security_log USING btree (at)"
                ]
            command.downgrade(config, "0039_analysis_metrics")
            with admin.connect() as conn:
                assert index_definitions(conn) == []
            command.upgrade(config, "head")
            with admin.connect() as conn:
                assert len(index_definitions(conn)) == 1
    finally:
        admin.dispose()


def test_the_abuse_window_queries_use_the_index(owner_database_url: str, database_url: str) -> None:
    """With a week of rows and a one-hour window, every window query of the view range-scans
    ``security_log_at`` (no ``owner_id`` filter, so ``(owner_id, at)`` cannot serve it)."""
    admin = create_engine(database_url)
    try:
        with migrated(owner_database_url):
            try:
                with admin.begin() as conn:
                    owner = new_user(conn)
                    conn.execute(
                        text(
                            "INSERT INTO security_log "
                            "(owner_id, session_id, ip_hash, method, route, status, at) "
                            "SELECT :o, gen_random_uuid(), decode(repeat('ab', 32), "
                            "'hex'), 'GET', '/api/classes', 200, "
                            "now() - make_interval(secs => g * 30) FROM generate_series(0, 20000) g"
                        ),
                        {"o": owner},
                    )
                with admin.connect() as conn:
                    conn.execute(text("ANALYZE security_log"))
                    for name, query in WINDOW_QUERIES.items():
                        params = {"minutes": 60, "n": 20, "min_networks": abuse.MIN_NETWORKS}
                        root = plan_of(conn, query.text, params)
                        seq = [
                            n
                            for n in plan_nodes(root)
                            if n["Node Type"] == "Seq Scan"
                            and n.get("Relation Name") == "security_log"
                        ]
                        assert not seq, name
                        assert INDEX in json.dumps(root), name
                    purge = plan_of(
                        conn,
                        securitylog._PURGE.text,  # pyright: ignore[reportPrivateUsage]
                        {"owner_id": owner, "days": 7},
                    )
                    # The planner picks ``security_log_at`` when one owner holds every row (as
                    # here) and ``security_log_owner_at`` when many do; either is a range scan
                    # of the 7-day cut-off, never a read of the whole table.
                    assert not [
                        n
                        for n in plan_nodes(purge)
                        if n["Node Type"] == "Seq Scan" and n.get("Relation Name") == "security_log"
                    ], "purge"
                    used = {n["Index Name"] for n in plan_nodes(purge) if "Index Name" in n}
                    assert used & {INDEX, OWNER_INDEX}, used
            finally:
                with admin.begin() as conn:
                    conn.execute(text("DELETE FROM users"))
    finally:
        admin.dispose()
