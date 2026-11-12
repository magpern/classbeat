"""What ``db.system_statement`` runs: an allowlist over the whole statement (#1006).

``check_system_write`` accepts one ``INSERT`` (the Postgres one, with or without its ``ON
CONFLICT`` clause), ``UPDATE`` or ``DELETE`` whose target is a ``Table`` in ``SYSTEM_TABLES`` with
no schema, and refuses everything else. Its clauses (single-row and multi-row ``VALUES`` row by row
and value by value, ``SET``, ``WHERE``, ``RETURNING``, and the ``ON CONFLICT`` target columns and
``DO UPDATE SET``) may hold only these elements:

- bind parameters (data, never SQL), and plain Python values the statement has not wrapped yet;
- columns of the target table itself (no other table, alias, ``column()`` or literal column);
- labels, groupings, function argument lists, ``AND``;
- the comparison and arithmetic operators in ``_OPERATORS``;
- the functions in ``_FUNCTIONS`` (what the limiter's statements use), not schema-qualified, and
  ``EXTRACT(epoch ...)``.

So ``RETURNING`` gives the target's own columns and values computed from them, nothing else. Any
other element (a subquery or any selectable, of a system table too, a CTE, ``text()``, other
functions, ``IN``, ``OR``) is refused where it is found. Every other part of the statement
(prefixes, hints, CTEs, ``INSERT ... FROM SELECT``, a named ``ON CONFLICT`` constraint or either
``ON CONFLICT ... WHERE``, extra ``FROM``s, return-defaults, options, and any part added by a later
SQLAlchemy) must be empty. The walk keeps a visited set, so it ends on any
construct, a self-referencing one too. Pure: no I/O.
"""

import operator
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BindParameter,
    BooleanClauseList,
    ClauseList,
    Column,
    Delete,
    Extract,
    Function,
    Grouping,
    Label,
    Table,
    Update,
)
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql.dml import OnConflictDoNothing, OnConflictDoUpdate
from sqlalchemy.sql import functions, operators
from sqlalchemy.sql.elements import BinaryExpression, ClauseElement

from classbeat.db.tables import SYSTEM_TABLES

REFUSED = "system_statement writes one system table only"
_FUNCTIONS = frozenset(
    {
        "to_timestamp",
        "floor",
        "ceil",
        "clock_timestamp",
        "make_interval",
        "greatest",
        "least",
        "now",
    }
)
_OPERATORS = frozenset(
    {operator.eq, operator.ne, operator.lt, operator.le, operator.gt, operator.ge}
    | {operator.add, operator.sub, operator.mul, operator.truediv}
)
_DATA = (int, float, bytes, str, datetime, bool, type(None))
_CLAUSES = frozenset({"_values", "_multi_values", "_where_criteria", "_returning"})
# Flags with no SQL of their own (a boolean or options of the CTEs, which must be absent).
_FLAGS = frozenset(
    {
        "_inline",
        "_sort_by_parameter_order",
        "include_insert_from_select_defaults",
        "_maintain_values_ordering",
        "_independent_ctes_opts",
        "dialect_options",
    }
)


def _refuse() -> ValueError:
    return ValueError(REFUSED)


def _children(element: object, target: Table) -> Iterable[object]:
    """The children of one allowed element; raises on any element not on the list."""
    if isinstance(element, ClauseElement):
        kind = type(element)
        if kind is BindParameter:
            if isinstance(element.value, ClauseElement):  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                raise _refuse()
            return ()
        if kind is Column:
            if element.table is not target:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                raise _refuse()
            return ()
        if kind in (Label, Grouping):
            return (element.element,)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        if kind is BinaryExpression and element.operator in _OPERATORS:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            return (element.left, element.right)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        if (kind is ClauseList and element.operator is operators.comma_op) or (  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            kind is BooleanClauseList and element.operator is operators.and_  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        ):
            return tuple(element.clauses)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
        if kind in (Function, functions.now) and _plain_function(element):
            return (element.clause_expr,)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        if kind is Extract and element.field == "epoch":  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            return (element.expr,)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        raise _refuse()
    if isinstance(element, _DATA):
        return ()
    raise _refuse()


def _plain_function(element: Any) -> bool:
    return element.name in _FUNCTIONS and not element.packagenames


def _walk(roots: Iterable[object], target: Table) -> None:
    """Every element under ``roots`` once (a visited set: a cycle ends the walk, never loops)."""
    pending, seen = list(roots), set[int]()
    while pending:
        element = pending.pop()
        if id(element) not in seen:
            seen.add(id(element))
            pending.extend(_children(element, target))


def _own_column(key: object, target: Table) -> bool:
    """A target column, as itself or by its name (``values(count=...)``)."""
    if isinstance(key, str):
        return key in target.c
    return key is target.c.get(getattr(key, "key", None) or "")


def _assignments(values: object, target: Table) -> list[object]:
    """The values of one ``SET`` or ``VALUES`` row; its keys must be target columns."""
    if isinstance(values, list | tuple):
        return list(values)  # pyright: ignore[reportUnknownArgumentType]  # a positional row
    if not isinstance(values, dict) or not all(_own_column(k, target) for k in values):  # pyright: ignore[reportUnknownVariableType]
        raise _refuse()
    return list(values.values())  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]


def _clause(name: str, value: Any, target: Table) -> list[object]:
    if not value:
        return []
    if name == "_values":
        return _assignments(value, target)
    if name == "_multi_values":  # walked row by row, value by value (``begin_together``)
        return [v for batch in value for row in batch for v in _assignments(row, target)]
    return list(value)  # _where_criteria, _returning: tuples of expressions


def _on_conflict(clause: object, target: Table) -> list[object]:
    """``ON CONFLICT (target columns) DO NOTHING | DO UPDATE SET ...``, with no ``WHERE``."""
    if clause is None:
        return []
    if type(clause) not in (OnConflictDoNothing, OnConflictDoUpdate):
        raise _refuse()
    conflict: Any = clause
    if (
        conflict.constraint_target is not None
        or conflict.inferred_target_whereclause is not None
        or not all(_own_column(c, target) for c in conflict.inferred_target_elements or ())
    ):
        raise _refuse()
    if type(clause) is OnConflictDoNothing:
        return []
    if conflict.update_whereclause is not None:
        raise _refuse()
    return _assignments(conflict.update_values_to_set, target)


def check_system_write(statement: object) -> None:
    """Raise ``ValueError`` unless ``statement`` is on the allowlist of this module's docstring."""
    if type(statement) not in (Insert, Update, Delete):
        raise _refuse()
    target: Any = getattr(statement, "table", None)
    if type(target) is not Table or target.schema is not None or target.name not in SYSTEM_TABLES:
        raise _refuse()
    roots: list[object] = []
    for name, _ in type(statement)._traverse_internals:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
        value = getattr(statement, name, None)
        if name in _CLAUSES:
            roots.extend(_clause(name, value, target))
        elif name == "_post_values_clause":
            roots.extend(_on_conflict(value, target))
        elif name != "table" and name not in _FLAGS and value:
            raise _refuse()  # prefixes, hints, CTEs, a SELECT, extra FROMs, options, or unknown
    _walk(roots, target)
