"""Guard 8 of ADR 0003 section 1: only ``classbeat.auth`` touches the account tables.

Purpose and limits. Like ADR 0002's AST guard for ``UserContext``, this catches *accidental*
misuse and makes misuse visible in review; it does not stop deliberately malicious or
obfuscated code. Known limits: rebinding through arbitrary variables or containers, computed
``getattr`` names, ``sys.modules`` lookups, ``exec``/``eval``, SQL assembled from pieces at run
time or read from a file, config or the database (runtime SQL), SQL in ``bytes`` literals (only
``str`` constants are scanned), and anything else a static scan of one file cannot follow. The
review gate (AGENTS.md) catches those. A subquery before the table in a FROM list
(``FROM a, (select 1) b, users``) and ``JOIN (users)`` are not followed either.

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
- any string expression that is SQL: a constant, an f-string (each expression is a
  placeholder) or a ``+`` chain of strings, taken as one text, so a verb and a table in
  different pieces still count. It must contain an SQL statement verb (select, insert, update,
  delete, copy, truncate, alter, merge, with, lock, drop, create, grant, revoke) as a whole word
  and an auth table name (optionally double-quoted, with any schema qualifier) right after an
  SQL keyword (from, join, into, update, table, copy, using, truncate, lock; after ``ON`` for
  grant/revoke; comments skipped; ``ONLY`` and ``IF EXISTS`` allowed; no space is needed before
  a quoted name) or later in a ``FROM a, b`` list. Without a verb, a fragment is still refused
  when it starts with the keyword and table (``" FROM users"``) or when the table is followed by
  a clause keyword or ``;`` (``" FROM users WHERE id = :id"``). Prose such as
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
    r"\b(?:select|insert|update|delete|copy|truncate|alter|merge|with|lock|drop|create|grant"
    r"|revoke)\b",
    re.IGNORECASE,
)
_GRANTS = re.compile(r"\b(?:grant|revoke)\b", re.IGNORECASE)
# A table name, optionally quoted and qualified with any schema: public.users, "s"."users".
_TABLE = r"(?:(?:\"[^\"]+\"|\w+)\s*\.\s*)?\"?(?P<name>" + "|".join(TABLE_NAMES) + r")\"?(?![\w$])"
_KEYWORD = r"(?<![\w$])(?:from|join|into|update|table|copy|using|truncate|lock)"
_GAP = r"(?:\s+|(?=\"))"  # Postgres reads FROM"users" too
_MODIFIERS = r"(?:(?:only|if\s+exists)\s+)*"
_AFTER = _GAP + _MODIFIERS + _TABLE
# A table name right after an SQL keyword.
SQL_AFTER_KEYWORD = re.compile(_KEYWORD + _AFTER, re.IGNORECASE)
# GRANT/REVOKE ... ON users
SQL_AFTER_ON = re.compile(r"(?<![\w$])on" + _AFTER, re.IGNORECASE)
# A table name later in a FROM list: ``FROM a, b x, users``.
SQL_IN_FROM_LIST = re.compile(
    r"(?<![\w$])from\s+(?:[\w\".$]+(?:\s+(?:as\s+)?[\w\"]+)?\s*,\s*)+" + _TABLE,
    re.IGNORECASE,
)
# Without a statement verb (a fragment of a statement built from pieces): the keyword and table
# at the very start, or followed by SQL continuation (a clause keyword or ``;``), not by prose.
_CONTINUATION = (
    r"(?:\s+(?:as\s+)?[\w\"]+)?\s+(?:where|on|set|values|order\s+by|group\s+by|limit|returning"
    r"|join|inner|left|right|cross|natural|full|using|select)\b|\s*;"
)
SQL_FRAGMENT_START = re.compile(_KEYWORD + _AFTER, re.IGNORECASE)
SQL_FRAGMENT_CONTINUED = re.compile(
    _KEYWORD + _AFTER + r"(?=" + _CONTINUATION + r")", re.IGNORECASE
)
_SQL_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
PLACEHOLDER = " {} "


def sql_table_in(text: str) -> str | None:
    """The auth table an SQL-looking string names, or None.

    ``text`` is a whole string: one constant, an f-string with a placeholder for each
    expression, or a ``+`` chain of strings joined the same way.
    """
    text = _SQL_COMMENT.sub(" ", text)
    if SQL_VERBS.search(text):
        match = SQL_AFTER_KEYWORD.search(text) or SQL_IN_FROM_LIST.search(text)
        if match is None and _GRANTS.search(text):
            match = SQL_AFTER_ON.search(text)
    else:
        match = SQL_FRAGMENT_START.match(text.lstrip()) or SQL_FRAGMENT_CONTINUED.search(text)
    return match.group("name") if match else None


def _string_text(node: ast.AST) -> tuple[str, bool]:
    """``(text, has_string)`` of a string expression: constants, f-strings and ``+`` chains."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.JoinedStr):
        parts = [
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else PLACEHOLDER
            for v in node.values
        ]
        return "".join(parts), True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, left_str = _string_text(node.left)
        right, right_str = _string_text(node.right)
        return left + right, left_str or right_str
    return PLACEHOLDER, False


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
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (node.value == TABLES_MODULE or _is_auth_submodule(node.value))
        ):
            found.append((node.lineno, f"string reference to {node.value}"))
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            text, has_string = _string_text(node)
            if has_string and (table := sql_table_in(text)):
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
