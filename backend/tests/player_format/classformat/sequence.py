"""Publication sequencing (ADR 0003 section 6). Pure: no I/O.

`stored` is what the backend holds for one (source, source_class_id): the highest accepted
publication number, whether it was a removal, and its content hash (None for a removal).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["accepted", "repeat", "stale", "conflict"]


@dataclass(frozen=True)
class Stored:
    number: int
    removed: bool
    content_hash: str | None


@dataclass(frozen=True)
class Incoming:
    number: int
    removed: bool
    content_hash: str | None


def decide(stored: Stored | None, incoming: Incoming) -> Outcome:
    """Decide what the backend does with an incoming publication.

    - nothing stored, or a higher number (also after a removal): accepted
    - a lower number: stale (refused)
    - the same number and the same content (same hash, or both removals): repeat, no change
    - the same number and anything else (other hash, removal vs document): conflict (refused)
    """
    if stored is None or incoming.number > stored.number:
        return "accepted"
    if incoming.number < stored.number:
        return "stale"
    if incoming.removed == stored.removed and incoming.content_hash == stored.content_hash:
        return "repeat"
    return "conflict"
