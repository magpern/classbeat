"""An owner allowlist setting of a spotdown rollout step (ADR 0040 section 9): a comma list of user
ids. Pure: no I/O.

UUIDs in the canonical 8-4-4-4-12 form (any case; spaces around a comma ignored), at most
``MAX_OWNERS``, no repeats; empty (the default) is **nobody**. No value means "everyone": a default
switch is not offered by an allowlist. A refusal names the setting and the rule, never the value,
and the process refuses to start.

Used by ``CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS`` (``song_match_config``, SD-P6, #1303).
"""

from typing import Final
from uuid import UUID

MAX_OWNERS: Final = 200


class OwnerListRefused(ValueError):
    """The setting breaks a rule; the message names the setting, never the value."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} must be a comma list of at most {MAX_OWNERS} distinct user ids")


def _one(part: str, name: str) -> UUID:
    try:
        owner = UUID(part)
    except ValueError:
        raise OwnerListRefused(name) from None
    if str(owner) != part.lower():  # only the canonical form: no braces, no urn, no bare hex
        raise OwnerListRefused(name)
    return owner


def parse_owners(value: str, name: str) -> frozenset[UUID]:
    """The owners of a checked value (``frozenset()`` for an empty one)."""
    parts = [part.strip() for part in value.split(",")] if value.strip() else []
    owners = [_one(part, name) for part in parts]
    if len(owners) > MAX_OWNERS or len(owners) != len(set(owners)):
        raise OwnerListRefused(name)
    return frozenset(owners)


def canonical(value: str, name: str) -> str:
    """The setting's validator: the ids, lower case, sorted and comma-joined (``""`` for none)."""
    return ",".join(sorted(str(owner) for owner in parse_owners(value, name)))
