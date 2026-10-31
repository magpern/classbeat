"""A tool call's ``arguments`` text as the loop's input, for both OpenAI-SDK adapters (ADR 0012
§3, amended 2026-10-31, #299).

JSON text nested deeper than ``MAX_ARGUMENT_DEPTH``, or text that is not JSON (``json.loads``'s
own ``RecursionError`` included), goes on as the text it is. The loop then answers
``invalid_input``, a tool error the model can correct, never a crash or ``internal``. The depth is
measured without recursion.
"""

import json
from typing import Any, Final

# A tool's arguments nested deeper than this are not parsed (every stage's schema is a few levels
# deep); far below the depth at which ``json.loads`` or the null-stripping would recurse too far.
MAX_ARGUMENT_DEPTH: Final = 32


def _deeper_than(value: Any, limit: int) -> bool:
    """Whether ``value`` nests lists and objects more than ``limit`` levels deep (no recursion)."""
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, dict | list):
            if depth > limit:
                return True
            items: Any = node.values() if isinstance(node, dict) else node
            pending += [(item, depth + 1) for item in items]
    return False


def parse_arguments(arguments: Any) -> tuple[bool, Any]:
    """``(True, value)`` for a call's ``arguments``: JSON text nested at most
    ``MAX_ARGUMENT_DEPTH`` deep. Else ``(False, arguments)``: what is not text, not JSON or nested
    too deep (``RecursionError`` included) goes on as it came, for the loop to answer
    ``invalid_input``."""
    if not isinstance(arguments, str):
        return False, arguments
    try:
        parsed = json.loads(arguments)
    except (ValueError, RecursionError):
        return False, arguments
    if _deeper_than(parsed, MAX_ARGUMENT_DEPTH):
        return False, arguments
    return True, parsed
