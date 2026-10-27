"""Guard 5 of ADR 0002 section 6: ``UserContext`` may only be annotated outside its issuers.

``scan_source`` parses Python source and returns the lines that reference ``UserContext`` other
than in a type annotation or an import used only for annotations. Calls (also through an alias),
subclassing, ``dataclasses.replace`` and ``functools.partial`` are all references outside an
annotation, so they are refused.
"""

import ast

NAME = "UserContext"
CONTEXT_MODULE = "classbeat.context"
# Modules whose own source may build a context: the two issuers (ADR 0002 section 1).
ISSUER_PACKAGES = ("classbeat/auth/", "classbeat/jobs/")
DEFINITION = "classbeat/context.py"
COPYING_FUNCTIONS = {"dataclasses": "replace", "functools": "partial"}


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _copying_names(tree: ast.AST) -> set[str]:
    """Local spellings of dataclasses.replace and functools.partial."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in COPYING_FUNCTIONS:
                    names.add(f"{alias.asname or alias.name}.{COPYING_FUNCTIONS[alias.name]}")
        elif isinstance(node, ast.ImportFrom) and node.module in COPYING_FUNCTIONS:
            for alias in node.names:
                if alias.name == COPYING_FUNCTIONS[node.module]:
                    names.add(alias.asname or alias.name)
    return names


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

    # Copying a context with replace() or partial() is refused in any file that handles one.
    copiers = _copying_names(tree) if NAME in source else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in copiers:
            found.append((node.lineno, f"call to {_dotted(node.func)}"))

    for node in ast.walk(tree):
        if id(node) in annotation_nodes:
            continue
        if isinstance(node, ast.Name) and node.id in aliases:
            found.append((node.lineno, f"reference to {node.id}"))
        elif isinstance(node, ast.Attribute) and node.attr == NAME:
            found.append((node.lineno, f"reference to {_dotted(node) or NAME}"))
        elif isinstance(node, ast.Constant) and node.value == NAME:
            # A string such as getattr(module, "UserContext"); string annotations are skipped.
            found.append((node.lineno, "string reference to UserContext"))
    return sorted(set(found))
