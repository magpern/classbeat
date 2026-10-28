"""Guard 5 of ADR 0002 section 6: building or copying a ``UserContext`` outside its issuers.

``scan_source`` parses Python source and returns what it refuses:

- any reference to ``UserContext`` that is not a type annotation or an import used only for
  annotations (calls, also through an alias, subclassing, ``isinstance``, ``cast``, strings);
- any reference to the issuer token (``issuer_token``, ``_ISSUER``);
- ``dataclasses.replace`` (also imported as ``replace``), ``object.__setattr__`` and
  ``object.__new__``, ``x.__class__(...)`` and ``type(x)(...)``, in any module, whether or not
  it mentions ``UserContext``; ``functools.partial`` in a module that does.

This layer and the runtime barrier in ``classbeat.context`` stop accidental misuse. Deliberately
malicious code inside the process is out of scope; the review gate catches it.
"""

import ast

NAME = "UserContext"
TOKEN_NAMES = {"issuer_token", "_ISSUER"}
CONTEXT_MODULE = "classbeat.context"
# Modules whose own source may build a context: the two issuers (ADR 0002 section 1).
ISSUER_PACKAGES = ("classbeat/auth/", "classbeat/jobs/")
DEFINITION = "classbeat/context.py"
# module -> function: refused everywhere / refused where the module handles a UserContext.
ALWAYS_REFUSED = {"dataclasses": "replace"}
REFUSED_WITH_CONTEXT = {"functools": "partial"}
REFUSED_BUILTIN_CALLS = {"object.__setattr__", "object.__new__", "object.__delattr__"}


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _spellings(tree: ast.AST, functions: dict[str, str]) -> set[str]:
    """Local spellings of the given ``module -> function`` pairs."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in functions:
                    names.add(f"{alias.asname or alias.name}.{functions[alias.name]}")
        elif isinstance(node, ast.ImportFrom) and node.module in functions:
            for alias in node.names:
                if alias.name == functions[node.module]:
                    names.add(alias.asname or alias.name)
    return names


def _forging_call(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "__class__":
        return "call of x.__class__(...)"
    if (
        isinstance(func, ast.Call)
        and isinstance(func.func, ast.Name)
        and func.func.id == "type"
        and len(func.args) == 1
    ):
        return "call of type(x)(...)"
    return None


def scan_source(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    annotation_nodes: set[int] = set()

    def mark(annotation: ast.AST | None) -> None:
        if annotation is not None:
            annotation_nodes.update(id(n) for n in ast.walk(annotation))

    for node in ast.walk(tree):
        if isinstance(node, ast.arg | ast.AnnAssign):
            mark(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            mark(node.returns)

    aliases = {NAME}  # local names bound to the class by an import
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == CONTEXT_MODULE:
            for alias in node.names:
                if alias.name == "*":
                    found.append((node.lineno, "star import of classbeat.context"))
                elif alias.name == NAME and alias.asname:
                    aliases.add(alias.asname)
                elif alias.name in TOKEN_NAMES:
                    found.append((node.lineno, f"import of {alias.name}"))

    refused = _spellings(tree, ALWAYS_REFUSED) | (
        _spellings(tree, REFUSED_WITH_CONTEXT) if NAME in source else set()
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _dotted(node.func)
        if callee in refused or callee in REFUSED_BUILTIN_CALLS:
            found.append((node.lineno, f"call to {callee}"))
        elif (why := _forging_call(node)) is not None:
            found.append((node.lineno, why))

    for node in ast.walk(tree):
        if id(node) in annotation_nodes:
            continue
        if isinstance(node, ast.Name) and (node.id in aliases or node.id in TOKEN_NAMES):
            found.append((node.lineno, f"reference to {node.id}"))
        elif isinstance(node, ast.Attribute) and (node.attr == NAME or node.attr in TOKEN_NAMES):
            found.append((node.lineno, f"reference to {_dotted(node) or node.attr}"))
        elif isinstance(node, ast.Constant) and (node.value == NAME or node.value in TOKEN_NAMES):
            # A string such as getattr(module, "UserContext"); string annotations are skipped.
            found.append((node.lineno, f"string reference to {node.value}"))
    return sorted(set(found))
