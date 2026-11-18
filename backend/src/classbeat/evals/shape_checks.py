"""The ``kept_shape`` revision check (ADR 0038 section 5, #1188): the composer's design on the
songs keeps the sketch's shape. Pure.

The revised class has the base's segments in the same order (each segment's ``kind``) and the
same number of main segments: the songs re-time the intervals, they do not reorder or drop parts.
The other revision checks (``kept_length``, ``kept_cooldown``, ``kept_efforts``) still apply.
"""

from typing import Any


def kept_shape(spec: dict[str, Any], base: dict[str, Any]) -> tuple[bool, str]:
    now = [str(s["kind"]) for s in spec["segments"]]
    was = [str(s["kind"]) for s in base["segments"]]
    return now == was, f"segments {now}, the base {was}"
