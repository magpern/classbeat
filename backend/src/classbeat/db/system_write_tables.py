"""The ``Table`` objects ``db.system_statement`` may write (#1013), matched by identity.

``statement_policy`` accepts a statement only if its target is the very object registered here
under its name, so a second ``Table("rate_limits", ...)`` with other column types or defaults
(built anywhere, with any ``MetaData``) is refused. The module that defines a system table's
writes registers it at import (``classbeat.auth.limit_writes`` registers ``rate_limits``). The
first registration of a name wins: registering a different object under it later raises, so a
copy can never replace the real table. A name must be in ``SYSTEM_TABLES``, with no schema.
"""

from types import MappingProxyType

from sqlalchemy import Table

from classbeat.db.tables import SYSTEM_TABLES

_REGISTERED: dict[str, Table] = {}


def register_system_write_table(table: Table) -> None:
    """Allow ``system_statement`` to write ``table`` (this object only); idempotent."""
    if type(table) is not Table or table.schema is not None or table.name not in SYSTEM_TABLES:
        raise ValueError("only a system table, with no schema, can be registered")
    if _REGISTERED.setdefault(table.name, table) is not table:
        raise ValueError(f"another table is already registered as {table.name}")


def system_write_tables() -> MappingProxyType[str, Table]:
    """The registered tables by name (read-only)."""
    return MappingProxyType(_REGISTERED)
