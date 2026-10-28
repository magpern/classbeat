"""Guard 8 of ADR 0003 section 1: only ``classbeat.auth`` touches the account tables.

``scan_source`` parses Python source (a module outside ``classbeat/auth/``) and returns what it
refuses:

- any import of ``classbeat.auth.tables`` (``import``, ``from ... import``, ``from classbeat.auth
  import tables``, ``importlib.import_module("classbeat.auth.tables")``) and any attribute
  access ``classbeat.auth.tables`` or ``auth.tables``;
- any string that is raw SQL naming one of the tables after ``FROM``, ``JOIN``, ``INTO``,
  ``UPDATE``, ``TABLE``, ``TRUNCATE``, ``ONLY`` or ``LOCK`` (optionally schema-qualified or
  quoted).

The worker's purge and the CLI call functions in ``classbeat.auth``; they never get here.
"""

import ast
import re

TABLES_MODULE = "classbeat.auth.tables"
AUTH_PACKAGE = "classbeat.auth"
TABLE_NAMES = (
    "users",
    "identities",
    "sessions",
    "known_devices",
    "pending_signups",
    "auth_tokens",
    "oauth_flows",
    "invites",
    "mfa_totp",
    "mfa_recovery_codes",
    "rate_limits",
    "auth_events",
)
SQL_NAMING_TABLE = re.compile(
    r"\b(?:from|join|into|update|table|truncate|only|lock|delete\s+from)\s+"
    r"(?:public\s*\.\s*)?[\"`]?(" + "|".join(TABLE_NAMES) + r")\b",
    re.IGNORECASE,
)


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def scan_source(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    auth_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == TABLES_MODULE or alias.name.startswith(TABLES_MODULE + "."):
                    found.append((node.lineno, f"import of {alias.name}"))
                elif alias.name == AUTH_PACKAGE and alias.asname:
                    auth_aliases.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == TABLES_MODULE or module.startswith(TABLES_MODULE + "."):
                found.append((node.lineno, f"import from {module}"))
            elif module == AUTH_PACKAGE:
                for alias in node.names:
                    if alias.name in ("tables", "*"):
                        found.append((node.lineno, f"import of {alias.name} from {AUTH_PACKAGE}"))
            elif module == "classbeat":
                for alias in node.names:
                    if alias.name == "auth":
                        auth_aliases.add(alias.asname or "auth")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "tables":
            dotted = _dotted(node)
            if dotted is not None and (
                dotted == TABLES_MODULE or dotted.rsplit(".", 1)[0] in auth_aliases | {AUTH_PACKAGE}
            ):
                found.append((node.lineno, f"reference to {dotted}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == TABLES_MODULE or node.value.startswith(TABLES_MODULE + "."):
                found.append((node.lineno, "string reference to classbeat.auth.tables"))
            match = SQL_NAMING_TABLE.search(node.value)
            if match is not None:
                found.append((node.lineno, f"raw SQL naming table {match.group(1)}"))
    return sorted(set(found))
