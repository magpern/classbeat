"""Guard 12 (ADR 0014 section 3): every reference to a user is decided, from the live schema.

``check_user_references`` reads the catalog, not a list: it finds every foreign key to ``users``
and every ``uuid`` column whose name reads as a user reference, and compares them with
``classbeat.account.coverage``. ``check_export_registry`` does the same for the export: every
owned table is exported (minus the columns it omits) or has a stated reason, and no ``bytea``
column (hash, ciphertext) can be exported.
"""

from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.account.coverage import USER_COLUMN_PATTERN
from classbeat.account.registry import Section


def check_user_references(
    conn: Connection,
    references: Mapping[tuple[str, str], str],
    unlinked: frozenset[tuple[str, str]],
) -> list[str]:
    problems: list[str] = []
    actual = {
        (table, column): action
        for table, column, action in conn.execute(
            text(
                "SELECT c.conrelid::regclass::text, a.attname, c.confdeltype::text "
                "FROM pg_constraint c JOIN pg_attribute a "
                "ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "WHERE c.contype = 'f' AND c.confrelid = 'public.users'::regclass"
            )
        )
    }
    for key in sorted(set(actual) - set(references)):
        problems.append(f"{key[0]}.{key[1]}: foreign key to users with no decision (ADR 0014)")
    for key in sorted(set(references) - set(actual)):
        problems.append(f"{key[0]}.{key[1]}: listed but not a foreign key to users")
    for key in sorted(set(actual) & set(references)):
        if actual[key] != references[key]:
            problems.append(
                f"{key[0]}.{key[1]}: database says {actual[key]}, list says {references[key]}"
            )
    looks_like_users = {
        (table, column)
        for table, column in conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type = 'uuid' "
                "AND column_name ~ :pattern"
            ),
            {"pattern": USER_COLUMN_PATTERN},
        )
    }
    for key in sorted(looks_like_users - set(actual) - unlinked):
        problems.append(f"{key[0]}.{key[1]}: a user-looking uuid, no foreign key, no decision")
    for key in sorted(unlinked - looks_like_users):
        problems.append(f"{key[0]}.{key[1]}: listed as an unlinked user id but not a uuid column")
    for key in sorted(unlinked & set(actual)):
        problems.append(f"{key[0]}.{key[1]}: listed as unlinked but has a foreign key")
    return problems


def check_export_registry(
    conn: Connection,
    sections: tuple[Section, ...],
    not_exported: Mapping[str, str],
    system_tables: Mapping[str, str],
) -> list[str]:
    problems: list[str] = []
    columns: dict[str, dict[str, str]] = {}
    for table, column, kind in conn.execute(
        text(
            "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p') "
            "AND a.attnum > 0 AND NOT a.attisdropped"
        )
    ):
        columns.setdefault(table, {})[column] = kind
    owned = {table for table in columns if table not in system_tables}
    exported = {section.table: section for section in sections}
    for table in sorted(owned - set(exported) - set(not_exported)):
        problems.append(f"{table}: owned table neither exported nor given a reason (ADR 0014)")
    for table in sorted((set(exported) | set(not_exported)) - owned):
        problems.append(f"{table}: export entry for a table that is not an owned table")
    for table in sorted(set(exported) & set(not_exported)):
        problems.append(f"{table}: both exported and not exported")
    for table, section in sorted(exported.items()):
        present = columns.get(table, {})
        for name in sorted(section.omit - set(present)):
            problems.append(f"{table}.{name}: omitted but not a column")
        for name in (part.strip() for part in section.order.split(",")):
            if name not in present:
                problems.append(f"{table}.{name}: ordered by a column that does not exist")
        if "owner_id" not in present:
            problems.append(f"{table}: exported but has no owner_id")
        for name, kind in sorted(present.items()):
            if kind == "bytea" and name not in section.omit:
                problems.append(f"{table}.{name}: bytea column would be exported")
    return problems
