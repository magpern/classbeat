"""``db.system_write_tables`` (#1013): the ``Table`` objects ``system_statement`` may write.

The limiter registers the real ``rate_limits`` at import; the first object registered under a
name is the only one accepted, so a copy can neither be registered nor replace it.
"""

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from classbeat.auth import limit_writes  # noqa: F401  # registers rate_limits at import
from classbeat.auth.tables import rate_limits
from classbeat.db import register_system_write_table
from classbeat.db.system_write_tables import system_write_tables


def test_the_limiter_registers_the_real_rate_limits_table() -> None:
    assert system_write_tables()["rate_limits"] is rate_limits
    register_system_write_table(rate_limits)  # idempotent for the same object
    assert system_write_tables()["rate_limits"] is rate_limits


@pytest.mark.parametrize(
    "table",
    [
        Table("rate_limits", MetaData(), Column("count", Integer)),  # a copy of the real one
        Table("rate_limits", MetaData(), Column("count", Integer), schema="other"),
        Table("classes", MetaData(), Column("n", Integer)),  # not a system table
    ],
    ids=["copy", "schema", "owned"],
)
def test_no_other_table_can_be_registered(table: Table) -> None:
    with pytest.raises(ValueError, match=r"system table|already registered"):
        register_system_write_table(table)
    assert system_write_tables().get(table.name) in (None, rate_limits)


def test_the_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        system_write_tables()["rate_limits"] = rate_limits  # pyright: ignore[reportIndexIssue]
