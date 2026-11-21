"""``CLASSBEAT_SPOTDOWN_STUDIO_OWNERS``: the allowlist of rollout step 2 for studio tracks
analysed by spotdown (ADR 0040 section 9; SD-P5, #1302). Pure: no I/O.

A comma list of user ids (UUIDs, any case, spaces around a comma ignored), at most
``MAX_OWNERS``, no repeats; empty (the default) is nobody. A refusal names the rule, never the
value, and the process refuses to start.
"""

from typing import Final
from uuid import UUID

MAX_OWNERS: Final = 200
_NAME: Final = "spotdown_studio_owners"


class StudioOwnersRefused(ValueError):
    """The setting breaks a rule; the message names it, never the value."""

    def __init__(self) -> None:
        super().__init__(f"{_NAME} must be a comma list of at most {MAX_OWNERS} distinct user ids")


def _one(part: str) -> UUID:
    try:
        owner = UUID(part)
    except ValueError:
        raise StudioOwnersRefused from None
    if str(owner) != part.lower():  # only the canonical 8-4-4-4-12 form, no braces or urn
        raise StudioOwnersRefused
    return owner


def parse_owners(value: str) -> frozenset[UUID]:
    """The owners of a checked value (``frozenset()`` for an empty one)."""
    parts = [part.strip() for part in value.split(",")] if value.strip() else []
    owners = [_one(part) for part in parts]
    if len(owners) > MAX_OWNERS or len(owners) != len(set(owners)):
        raise StudioOwnersRefused
    return frozenset(owners)


def canonical(value: str) -> str:
    """The setting's validator: the ids, lower case, sorted and comma-joined (``""`` for none)."""
    return ",".join(sorted(str(owner) for owner in parse_owners(value)))
