"""The size of a JSON value as PostgreSQL prints it from ``jsonb`` (``octet_length(col::text)``),
for the checks that must agree with a table's size constraint (SR C5, the C4 review's carry-over:
``swedebeat_track_maps.map`` and ``swedebeat_mix_maps.map``). Pure: no I/O.

``jsonb`` prints ``", "`` between items and ``": "`` after a key, strings in UTF-8 with the same
escapes as Python's ``json`` (``\\"``, ``\\\\``, ``\\b``, ``\\f``, ``\\n``, ``\\r``, ``\\t``, other
control characters as ``\\u00XX``), and a number in plain decimal notation, never with an exponent:
``1e-07`` is ``0.0000001`` there, which compact JSON measures as five bytes. So a value that fits a
compact measure can still break the table's check; this measure is the table's own.
"""

import json
import math
from decimal import Decimal
from typing import Any


def _number(value: int | float) -> int:
    if isinstance(value, int):
        return len(str(value))
    if not math.isfinite(value):
        raise ValueError("jsonb has no NaN or infinity")
    return len(format(Decimal(repr(value)), "f"))


def jsonb_text_size(value: Any) -> int:
    """``octet_length(CAST(json AS jsonb)::text)`` of ``value``; ``ValueError`` (or
    ``TypeError``) for a value JSON cannot hold."""
    if value is None or value is True:
        return 4
    if value is False:
        return 5
    if isinstance(value, int | float):
        return _number(value)
    if isinstance(value, str):
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    if isinstance(value, list | tuple):
        items: list[Any] = list(value)  # pyright: ignore[reportUnknownArgumentType]
        return 2 + sum(jsonb_text_size(v) for v in items) + 2 * max(len(items) - 1, 0)
    if isinstance(value, dict):
        pairs: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        size = 2 + 2 * max(len(pairs) - 1, 0)
        for key, item in pairs.items():
            if not isinstance(key, str):
                raise TypeError("a JSON object's keys are strings")
            size += jsonb_text_size(key) + 2 + jsonb_text_size(item)
        return size
    raise TypeError("not a JSON value")
