"""Column builders shared by the M1 system tables (``classbeat.auth.tables``, ADR 0003 section 1).

Pure definitions: no database, no I/O. Split out of ``tables`` on touch (#1265).
"""

from typing import Any

from sqlalchemy import Column, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID

_ID = "gen_random_uuid()"
_NOW = "now()"


def id_column() -> Column[Any]:
    return Column("id", UUID(as_uuid=True), primary_key=True, server_default=text(_ID))


def user_fk(*, nullable: bool = False, set_null: bool = False) -> Column[Any]:
    return Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL" if set_null else "CASCADE"),
        nullable=nullable or set_null,
    )


def timestamp(name: str, *, nullable: bool = True, default_now: bool = False) -> Column[Any]:
    return Column(
        name,
        DateTime(timezone=True),
        nullable=nullable,
        server_default=text(_NOW) if default_now else None,
    )
