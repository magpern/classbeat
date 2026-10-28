"""Guards of ADR 0004: who may import the envelope module and touch the key and connection tables.

Like guard 8 (``auth_tables_scan``) this catches accidental misuse and makes misuse visible in
review; it cannot stop obfuscated code (computed names, ``exec``, ``sys.modules``).

``scan_source(source, module)`` returns what ``module`` (a dotted name under ``classbeat``) may not
do:

- import ``classbeat.crypto.envelope`` (any form: ``import``, ``from ... import``, relative,
  ``from classbeat.crypto import envelope``, or the dotted name in a string, as
  ``importlib.import_module`` takes it) unless it is in ``classbeat.connections``,
  ``classbeat.auth`` or is ``classbeat.crypto.keks``;
- import the ``connection_keks`` table or ``metadata`` from ``classbeat.crypto.keks``, reach them
  as an attribute of ``keks``, or hold an SQL string naming ``connection_keks`` (only
  ``classbeat.crypto.keks`` may);
- import ``classbeat.connections.tables`` or its ``connections`` table, or hold an SQL string
  naming the ``connections`` table, outside ``classbeat.connections``.
"""

import ast
import re

ENVELOPE = "classbeat.crypto.envelope"
KEKS = "classbeat.crypto.keks"
CONNECTION_TABLES = "classbeat.connections.tables"
ENVELOPE_ALLOWED = ("classbeat.connections", "classbeat.auth", KEKS, ENVELOPE)

_SQL = r"\b(?:select|insert|update|delete|copy|truncate|alter|lock|drop|vacuum|grant|revoke)\b"
_KEYWORD = r"\b(?:from|join|into|update|table|copy|truncate|lock|vacuum|on|\(full\))\s+"
_SCHEMA = r"(?:\"?public\"?\s*\.\s*)?\"?"


def _sql_naming(table: str) -> re.Pattern[str]:
    return re.compile(rf"(?is)(?=.*{_SQL}).*{_KEYWORD}(?:\(full\)\s+)?{_SCHEMA}{table}\"?(?![\w$])")


KEK_SQL = _sql_naming("connection_keks")
CONNECTIONS_SQL = _sql_naming("connections")


def _inside(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _resolve(node: ast.ImportFrom, module: str) -> str:
    if not node.level:
        return node.module or ""
    parts = module.split(".")
    base = parts[: len(parts) - node.level]
    return ".".join([*base, node.module] if node.module else base)


def _strings(tree: ast.AST) -> list[tuple[int, str]]:
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "?"
                for v in node.values
            )
            found.append((node.lineno, text))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.append((node.lineno, node.value))
    return found


def scan_source(source: str, module: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    may_envelope = any(_inside(module, allowed) for allowed in ENVELOPE_ALLOWED)
    may_keks_table = module == KEKS
    may_connections_table = _inside(module, "classbeat.connections")
    keks_aliases: set[str] = set()
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ENVELOPE and not may_envelope:
                    found.append((node.lineno, "imports the envelope module"))
                if alias.name == CONNECTION_TABLES and not may_connections_table:
                    found.append((node.lineno, "imports the connections table module"))
                if alias.name == KEKS:
                    keks_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            source_module = _resolve(node, module)
            names = {alias.name for alias in node.names}
            if not may_envelope and (
                source_module == ENVELOPE
                or (source_module == "classbeat.crypto" and names & {"envelope", "*"})
            ):
                found.append((node.lineno, "imports the envelope module"))
            if not may_connections_table and (
                source_module == CONNECTION_TABLES
                or (source_module == "classbeat.connections" and names & {"tables", "*"})
            ):
                found.append((node.lineno, "imports the connections table module"))
            if (
                source_module == KEKS
                and not may_keks_table
                and names
                & {
                    "connection_keks",
                    "metadata",
                    "*",
                }
            ):
                found.append((node.lineno, "imports the connection_keks table"))
            if source_module == "classbeat.crypto" and "keks" in names:
                keks_aliases |= {a.asname or a.name for a in node.names if a.name == "keks"}
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in ("connection_keks", "metadata")
            and isinstance(node.value, ast.Name)
            and node.value.id in keks_aliases
            and not may_keks_table
        ):
            found.append((node.lineno, "uses the connection_keks table"))
    for line, text in _strings(tree):
        if not may_envelope and ENVELOPE in text:
            found.append((line, "names the envelope module"))
        if not may_keks_table and KEK_SQL.search(text):
            found.append((line, "SQL on connection_keks"))
        if not may_connections_table and CONNECTIONS_SQL.search(text):
            found.append((line, "SQL on connections"))
    return found
