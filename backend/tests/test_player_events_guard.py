"""Who reaches the ``player_events`` outbox (ADR 0034 section 2, #1058): a guard, no database.

- Only ``classbeat.player.outbox`` imports its ``Table`` (``classbeat.player.schema``).
- Only the account side (``classbeat.auth.player_events``), the delivery
  (``classbeat.player.delivery``) and the admin Jobs view (``classbeat.admin.jobs``) import the
  outbox.
- No module writes SQL against the table: no string constant names it after ``FROM``, ``INTO``,
  ``UPDATE``, ``JOIN``, ``TABLE`` or ``TRUNCATE``.
- ``classbeat.player`` imports nothing from ``classbeat.auth`` (guard 8 keeps its submodules to
  itself; the account side calls the outbox, never the other way round)."""

import ast
import re

from test_billing_guard import importers, modules

SCHEMA = "classbeat.player.schema"
OUTBOX = "classbeat.player.outbox"
OUTBOX_USERS = {"classbeat.auth.player_events", "classbeat.player.delivery", "classbeat.admin.jobs"}
SQL = re.compile(r"(?i)\b(from|into|update|join|table|truncate)\s+(public\.)?\"?player_events\b")


def test_only_the_outbox_imports_the_table() -> None:
    assert importers(SCHEMA) == {OUTBOX}


def test_only_the_account_side_the_delivery_and_the_admin_view_use_the_outbox() -> None:
    assert importers(OUTBOX) == OUTBOX_USERS


def test_no_module_writes_sql_against_the_table() -> None:
    found = [
        f"{name}:{node.lineno}"
        for name, tree in modules().items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL.search(node.value)
    ]
    assert found == []


def test_the_player_package_imports_nothing_of_auth() -> None:
    found = [
        f"{name}:{node.lineno}"
        for name, tree in modules().items()
        if name.startswith("classbeat.player")
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "classbeat.auth" or node.module.startswith("classbeat.auth."))
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module == "classbeat"
            and any(a.name == "auth" for a in node.names)
        )
        or (
            isinstance(node, ast.Import)
            and any(a.name.startswith("classbeat.auth") for a in node.names)
        )
    ]
    assert found == []


def test_the_sql_pattern_catches_a_write() -> None:
    assert SQL.search("DELETE FROM player_events WHERE sub = :s")
    assert SQL.search("insert into public.player_events (kind) values ('x')")
    assert SQL.search('UPDATE "player_events" SET state = 1')
    assert not SQL.search("classbeat.auth.player_events")
    assert not SQL.search("player_events_truncated")
