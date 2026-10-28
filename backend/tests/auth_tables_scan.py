"""Guard 8 of ADR 0003 section 1: only ``classbeat.auth`` touches the account tables.

Purpose and limits. Like ADR 0002's AST guard for ``UserContext``, this catches *accidental*
misuse and makes misuse visible in review; it does not stop deliberately malicious or
obfuscated code. Known limits: rebinding through arbitrary variables or containers, computed
``getattr`` names, ``sys.modules`` lookups, ``exec``/``eval``, SQL assembled from pieces at run
time or read from a file, config or the database (runtime SQL), SQL in ``bytes`` literals (only
``str`` constants are scanned), and anything else a static scan of one file cannot follow. The
review gate (AGENTS.md) catches those.

``scan_source`` parses Python source (a module outside ``classbeat/auth/``) and returns what it
refuses. Outside the package only the public API of ``classbeat.auth`` itself may be used:

- any import of a ``classbeat.auth`` submodule (``from classbeat.auth.sessions import ...``,
  ``import classbeat.auth.purge as p``, relative forms, ``importlib.import_module`` and
  ``__import__`` of one, absolute or relative with a package);
- any imported name that is a table, ``metadata``, a submodule name or ``*`` from
  ``classbeat.auth``; attribute access to such a name on ``classbeat.auth``, on an alias of it,
  on ``<alias of classbeat>.auth`` (``import classbeat as c; c.auth.tables``), or directly on
  ``importlib.import_module("classbeat.auth")`` (also when assigned to a name); and
  ``getattr(<such a root>, "<such a name>")``;
- ``vars(<root>)``, ``<root>.__dict__`` and ``pkgutil.resolve_name("classbeat.auth...")``;
- ``table(...)`` and ``Table(...)`` (sqlalchemy) naming an auth table, as first argument or as
  ``name=``;
- ``__import__("classbeat.auth")`` returns the top-level ``classbeat`` package, so
  ``__import__("classbeat.auth").auth.tables`` is followed like ``import classbeat``;
- any string constant (also the literal parts of f-strings) that contains an SQL statement verb
  (select, insert, update, delete, copy, truncate, alter, merge, with, lock, drop, create, grant)
  as a whole word and an auth table name (optionally double-quoted and schema-qualified) right
  after an SQL keyword (from, join, into, update, table, copy, using, truncate, lock; comments
  skipped; ``ONLY`` and ``IF EXISTS`` allowed) or later in a ``FROM a, b`` list. Prose such as
  "copied from the sessions of the user" or "copied from sessions" (no statement verb) is not
  flagged; "update the sessions" is not either (the table does not follow the keyword).

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
        "admin_routes",
        "clientip",
        "cookies",
        "deps",
        "devices",
        "email_routes",
        "events",
        "flowtools",
        "hooks",
        "identity_routes",
        "keys",
        "mails",
        "mfa",
        "mfa_routes",
        "middleware",
        "notices",
        "notifier",
        "oauth_client",
        "oauth_handoff",
        "oauth_idtoken",
        "oauth_outcomes",
        "oauth_providers",
        "oauth_routes",
        "oauth_store",
        "passwords",
        "purge",
        "ratelimit",
        "reset_routes",
        "routes",
        "sessions",
        "setup",
        "signin_routes",
        "signup_routes",
        "tables",
        "tokens",
        "totp",
    }
)
FORBIDDEN_NAMES = frozenset(TABLE_NAMES) | {"metadata"} | SUBMODULES
SQL_VERBS = re.compile(
    r"\b(?:select|insert|update|delete|copy|truncate|alter|merge|with|lock|drop|create|grant)\b",
    re.IGNORECASE,
)
_TABLE = r"(?:\"?public\"?\s*\.\s*)?\"?(?P<name>" + "|".join(TABLE_NAMES) + r")\"?(?![\w$])"
_KEYWORD = r"(?<![\w$])(?:from|join|into|update|table|copy|using|truncate|lock)"
_MODIFIERS = r"(?:(?:only|if\s+exists)\s+)*"
# A table name right after an SQL keyword, optionally quoted and schema-qualified.
SQL_AFTER_KEYWORD = re.compile(_KEYWORD + r"\s+" + _MODIFIERS + _TABLE, re.IGNORECASE)
# A table name later in a FROM list: ``FROM a, b x, users``.
SQL_IN_FROM_LIST = re.compile(
    r"(?<![\w$])from\s+(?:[\w\".$]+(?:\s+(?:as\s+)?[\w\"]+)?\s*,\s*)+" + _TABLE,
    re.IGNORECASE,
)
_SQL_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


def sql_table_in(text: str) -> str | None:
    """The auth table an SQL-looking string names, or None."""
    if not SQL_VERBS.search(text):
        return None
    text = _SQL_COMMENT.sub(" ", text)
    match = SQL_AFTER_KEYWORD.search(text) or SQL_IN_FROM_LIST.search(text)
    return match.group("name") if match else None


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


def _import_target(node: ast.AST) -> tuple[str, str] | None:
    """``(function, module)`` of ``import_module("m")`` / ``__import__("m", ...)``, else None."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    name = (_dotted(node.func) or "").rsplit(".", 1)[-1]
    first = node.args[0]
    is_import = name in ("import_module", "__import__")
    if is_import and isinstance(first, ast.Constant) and isinstance(first.value, str):
        return name, first.value
    return None


def _is_import_call(node: ast.AST, target: str = AUTH_PACKAGE) -> bool:
    """``importlib.import_module("classbeat.auth")`` or ``__import__("classbeat.auth", ...)``."""
    found = _import_target(node)
    return found is not None and found[1] == target


class _Roots:
    """Local names bound to the ``classbeat`` package and to ``classbeat.auth``."""

    def __init__(self, tree: ast.AST) -> None:
        self.package: set[str] = set()
        self.auth: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "classbeat":
                        self.package.add(alias.asname or "classbeat")
                    elif alias.name == AUTH_PACKAGE and alias.asname:
                        self.auth.add(alias.asname)
            elif isinstance(node, ast.ImportFrom):
                module = _module_of(node)
                from_package = module == "classbeat" or bool(node.level and not module)
                for alias in node.names:
                    if alias.name == "classbeat" and (node.level or not module):
                        self.package.add(alias.asname or "classbeat")
                    elif alias.name == "auth" and from_package:
                        self.auth.add(alias.asname or "auth")
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if self.is_auth(node.value):
                    self.auth.update(names)
                if self.is_package(node.value):
                    self.package.update(names)

    def is_package(self, node: ast.AST) -> bool:
        """True when ``node`` evaluates to the top-level ``classbeat`` package."""
        found = _import_target(node)
        if found is not None:
            function, module = found
            # __import__("a.b") without a fromlist returns the top-level package "a".
            return module == "classbeat" or (function == "__import__" and module == AUTH_PACKAGE)
        return isinstance(node, ast.Name) and node.id in self.package

    def is_auth(self, node: ast.AST) -> bool:
        """True when ``node`` evaluates to the ``classbeat.auth`` package."""
        if _is_import_call(node):  # __import__ is also kept here: conservative
            return True
        if isinstance(node, ast.Attribute) and node.attr == "auth" and self.is_package(node.value):
            return True
        dotted = _dotted(node)
        if dotted is None:
            return False
        head, _, rest = dotted.partition(".")
        if head in self.package:
            dotted = "classbeat" + ("." + rest if rest else "")
        return dotted == AUTH_PACKAGE or dotted in self.auth


def scan_source(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    roots = _Roots(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_auth_submodule(alias.name):
                    found.append((node.lineno, f"import of {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = _module_of(node)
            if _is_auth_submodule(module):
                found.append((node.lineno, f"import from {module}"))
            elif module == AUTH_PACKAGE:
                for alias in node.names:
                    if alias.name == "*" or alias.name in FORBIDDEN_NAMES:
                        found.append((node.lineno, f"import of {alias.name} from {AUTH_PACKAGE}"))
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES and roots.is_auth(node.value):
                found.append((node.lineno, f"reference to {node.attr} on classbeat.auth"))
            elif node.attr == "__dict__" and roots.is_auth(node.value):
                found.append((node.lineno, "__dict__ of classbeat.auth"))
        elif isinstance(node, ast.Call):
            found += _call_findings(node, roots)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == TABLES_MODULE or _is_auth_submodule(node.value):
                found.append((node.lineno, f"string reference to {node.value}"))
            if table := sql_table_in(node.value):
                found.append((node.lineno, f"raw SQL naming table {table}"))
    return sorted(set(found))


def _str_args(node: ast.Call) -> list[str]:
    values = [*node.args, *(k.value for k in node.keywords)]
    return [v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)]


def _table_name_arg(node: ast.Call) -> ast.AST | None:
    if node.args:
        return node.args[0]
    return next((k.value for k in node.keywords if k.arg == "name"), None)


def _call_findings(node: ast.Call, roots: _Roots) -> list[tuple[int, str]]:
    callee = _dotted(node.func) or ""
    name = callee.rsplit(".", 1)[-1]
    strings = _str_args(node)
    found: list[tuple[int, str]] = []
    if (
        name == "getattr"
        and len(node.args) > 1
        and roots.is_auth(node.args[0])
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in FORBIDDEN_NAMES
    ):
        found.append((node.lineno, f"getattr of {node.args[1].value} on classbeat.auth"))
    elif name == "vars" and node.args and roots.is_auth(node.args[0]):
        found.append((node.lineno, "vars() of classbeat.auth"))
    elif name == "resolve_name" and any(v.startswith(AUTH_PACKAGE) for v in strings):
        found.append((node.lineno, "pkgutil.resolve_name of classbeat.auth"))
    elif name in ("import_module", "__import__"):
        first = strings[0] if strings else ""
        package = strings[1] if len(strings) > 1 else ""
        if _is_auth_submodule(first) or (
            first.startswith(".") and package.startswith(AUTH_PACKAGE)
        ):
            found.append((node.lineno, f"dynamic import of {first}"))
    elif name in ("table", "Table"):
        arg = _table_name_arg(node)
        if isinstance(arg, ast.Constant) and arg.value in TABLE_NAMES:
            found.append((node.lineno, f"sqlalchemy {name}({arg.value!r})"))
    return found
