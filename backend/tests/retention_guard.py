"""Guard 10 (ADR 0007 section 11): every owned table says what it holds and how it is deleted.

``check_retention`` returns a list of violations (empty when the registry matches the schema). It
reads the catalog only, so it checks the real schema, whatever created it.
"""

from collections import defaultdict
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db.retention import Retention

# Types that can hold provider-derived or user-written content.
CONTENT_TYPES = ("text", "jsonb", "bytea", "character varying")


def check_retention(
    conn: Connection, retention: Mapping[str, Retention], system_tables: Mapping[str, str]
) -> list[str]:
    problems: list[str] = []
    columns: dict[str, dict[str, str]] = defaultdict(dict)
    for table, column, kind in conn.execute(
        text(
            "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
            "AND a.attnum > 0 AND NOT a.attisdropped"
        )
    ):
        columns[table][column] = kind
    owned = {table for table in columns if table not in system_tables}
    # Edges of ``ON DELETE CASCADE`` (referenced table -> referencing table).
    cascades: dict[str, set[str]] = defaultdict(set)
    for child, parent in conn.execute(
        text(
            "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint "
            "WHERE contype = 'f' AND confdeltype = 'c'"
        )
    ):
        cascades[parent].add(child)

    def reaches(start: str, table: str) -> bool:
        seen, todo = set(), [start]
        while todo:
            node = todo.pop()
            if node == table:
                return True
            if node not in seen:
                seen.add(node)
                todo.extend(cascades[node])
        return False

    for table in sorted(owned - set(retention)):
        problems.append(f"{table}: owned table with no RETENTION entry (ADR 0007 section 11)")
    for table in sorted(set(retention) - owned):
        problems.append(f"{table}: RETENTION entry for a table that is not an owned table")
    for table in sorted(owned & set(retention)):
        entry = retention[table]
        present = columns[table]
        if not entry.deleted_with:
            problems.append(f"{table}: no deletion path")
        for name in sorted((entry.columns | entry.reviewed) - set(present)):
            problems.append(f"{table}.{name}: listed in RETENTION but not a column")
        for name in sorted(entry.columns & entry.reviewed):
            problems.append(f"{table}.{name}: listed as both holding content and reviewed")
        for name, kind in sorted(present.items()):
            if kind in CONTENT_TYPES and name not in entry.columns | entry.reviewed:
                problems.append(f"{table}.{name}: {kind} column not classified in RETENTION")
        for path, root in (("class", "classes"), ("account", "users")):
            if path in entry.deleted_with and not reaches(root, table):
                problems.append(
                    f"{table}: claims deletion with the {path} but no cascade reaches it"
                )
    return problems
