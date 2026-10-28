"""Guard 8 of ADR 0003 section 1: only ``classbeat.auth`` touches the account tables.

``scan_source`` parses Python source (a module outside ``classbeat/auth/``) and returns what it
refuses. Outside the package only the public API of ``classbeat.auth`` itself may be used:

- any import of a ``classbeat.auth`` submodule (``from classbeat.auth.sessions import ...``,
  ``import classbeat.auth.purge as p``, relative forms, ``importlib.import_module`` and
  ``__import__`` of one, absolute or relative with ``package``);
- any imported name that is a table, ``metadata``, a submodule name or ``*`` from
  ``classbeat.auth``; attribute access to such a name on an alias of ``classbeat.auth`` (or on
  ``classbeat.auth`` itself); ``getattr(<alias>, "<such a name>")``;
- ``table("<auth table>")`` and ``Table("<auth table>", ...)`` (sqlalchemy);
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
SUBMODULES = frozenset(
    {
        "accounts",
        "clientip",
        "cookies",
        "deps",
        "devices",
        "events",
        "hooks",
        "keys",
        "middleware",
        "passwords",
        "purge",
        "ratelimit",
        "routes",
        "sessions",
        "setup",
        "tables",
    }
)
FORBIDDEN_NAMES = frozenset(TABLE_NAMES) | {"metadata"} | SUBMODULES
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


def _is_auth_submodule(name: str) -> bool:
    return name.startswith(AUTH_PACKAGE + ".")


def _module_of(node: ast.ImportFrom) -> str:
    module = node.module or ""
    if node.level and (module == "auth" or module.startswith("auth.")):
        return "classbeat." + module  # a relative import from a top-level classbeat module
    return module


def scan_source(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    aliases: set[str] = set()  # local names bound to the classbeat.auth package
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_auth_submodule(alias.name):
                    found.append((node.lineno, f"import of {alias.name}"))
                elif alias.name == AUTH_PACKAGE and alias.asname:
                    aliases.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            module = _module_of(node)
            if _is_auth_submodule(module):
                found.append((node.lineno, f"import from {module}"))
            elif module == AUTH_PACKAGE:
                for alias in node.names:
                    if alias.name == "*" or alias.name in FORBIDDEN_NAMES:
                        found.append((node.lineno, f"import of {alias.name} from {AUTH_PACKAGE}"))
            elif module == "classbeat" or (node.level and not module):
                for alias in node.names:
                    if alias.name == "auth":
                        aliases.add(alias.asname or "auth")
    roots = aliases | {AUTH_PACKAGE}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            dotted = _dotted(node)
            if dotted is not None and dotted.rsplit(".", 1)[0] in roots:
                found.append((node.lineno, f"reference to {dotted}"))
        elif isinstance(node, ast.Call):
            found += _call_findings(node, roots)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == TABLES_MODULE or _is_auth_submodule(node.value):
                found.append((node.lineno, f"string reference to {node.value}"))
            match = SQL_NAMING_TABLE.search(node.value)
            if match is not None:
                found.append((node.lineno, f"raw SQL naming table {match.group(1)}"))
    return sorted(set(found))


def _str_args(node: ast.Call) -> list[str]:
    values = [*node.args, *(k.value for k in node.keywords)]
    return [v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)]


def _call_findings(node: ast.Call, roots: set[str]) -> list[tuple[int, str]]:
    callee = _dotted(node.func) or ""
    name = callee.rsplit(".", 1)[-1]
    strings = _str_args(node)
    found: list[tuple[int, str]] = []
    if (
        name == "getattr"
        and len(node.args) > 1
        and (_dotted(node.args[0]) or "") in roots
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in FORBIDDEN_NAMES
    ):
        found.append((node.lineno, f"getattr of {node.args[1].value} on classbeat.auth"))
    elif name in ("import_module", "__import__"):
        first = strings[0] if strings else ""
        package = strings[1] if len(strings) > 1 else ""
        if _is_auth_submodule(first) or (
            first.startswith(".") and package.startswith(AUTH_PACKAGE)
        ):
            found.append((node.lineno, f"dynamic import of {first}"))
    elif name in ("table", "Table") and node.args:
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and first_arg.value in TABLE_NAMES:
            found.append((node.lineno, f"sqlalchemy {name}({first_arg.value!r})"))
    return found
