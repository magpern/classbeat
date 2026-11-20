"""Which ``analysis-v1`` features classbeat may validate with (ADR 0040 section 5, plan section 4),
and safe reads of the document. Pure: no I/O.

Every feature carries ``method`` and ``confidence``. **Only a ``measured`` or ``estimated`` feature
with a confidence of at least ``TRUST_MIN`` is trusted**: it may drive a backend check or a
classbeat map. A ``heuristic`` one (labels always are) or a low-confidence one is a **hint**: it
may reach the model marked as a hint and never drives a check.

The client checked only the document's top level (``answer_model.parse_analysis``), so every read
here is defensive: a missing or wrongly typed value reads as absent, never as an exception.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Final

TRUST_MIN: Final = 0.5
TRUSTED_METHODS: Final = frozenset({"measured", "estimated"})
METHODS: Final = TRUSTED_METHODS | {"heuristic"}


def obj(value: object, *keys: str) -> Mapping[str, object] | None:
    """``value[k1][k2]...`` when every step is an object."""
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return value if isinstance(value, Mapping) else None  # pyright: ignore[reportUnknownVariableType]


def items(value: object) -> list[Mapping[str, object]]:
    """The objects of a list (anything else in it is skipped); ``[]`` for a non-list."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, Mapping)]  # pyright: ignore[reportUnknownVariableType]


def num(value: object, low: float = -math.inf, high: float = math.inf) -> float | None:
    """A finite number within ``[low, high]``; never a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def word(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def numbers(value: object) -> list[float] | None:
    """A list of finite numbers, or ``None`` when any item is not one."""
    if not isinstance(value, list):
        return None
    out = [num(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return None if any(v is None for v in out) else [v for v in out if v is not None]


def confidence(feature: Mapping[str, object], key: str = "confidence") -> float | None:
    return num(feature.get(key), 0.0, 1.0)


def trusted(feature: Mapping[str, object] | None, key: str = "confidence") -> bool:
    """A ``measured`` or ``estimated`` feature at or above ``TRUST_MIN``."""
    if feature is None:
        return False
    method = feature.get("method")
    if not isinstance(method, str) or method not in TRUSTED_METHODS:
        return False
    value = confidence(feature, key)
    return value is not None and value >= TRUST_MIN


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
