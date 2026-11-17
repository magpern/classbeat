"""How ``spotify_replace_items`` (``classbeat.playlists.replace``) classifies a refused request:
the owner check's categories, and a refused item write to a public playlist. Moved out of
``replace`` (CLAUDE.md rule 2) when the second was added (#1155, roadmap Amendment 27).

- **The owner check** (the first request, before any write): Spotify's refusal (4xx), or a
  connection that is no longer the held one so nothing was sent at all (``NEVER_SENT``), proves
  nothing was written; its category by purpose. Anything else is uncertain.
- **An item write refused with 403 while the owner check read ``public: true``**: Spotify lets a
  token without ``playlist-modify-public`` read a public playlist but not write it (a connection
  made before Amendment 27 has only the two private scopes). ``failed`` with ``scope_mismatch``,
  whose sentence asks the user to connect Spotify again. The write is absolute, so a ``failed``
  row is safe to send again, and a list left partly written is what the reconciler would also
  call ``failed`` (readable and different). Any other refusal after a write may have been sent
  stays uncertain.
"""

from collections.abc import Mapping
from typing import Any, Final, cast

from classbeat.connections import ConnectionFailure
from classbeat.connections.errors import NEVER_SENT
from classbeat.jobs import failure_of

# The categories of a failed owner check, by purpose (ADR 0008 section 2): the pool's own words
# for "gone" and "changed"; a final playlist is one classbeat just created for the class.
MISSING: Final = {"pool": "pool_missing", "final": "not_found"}
UNUSABLE: Final = {"pool": "pool_unusable", "final": "forbidden"}
NO_PUBLIC_SCOPE: Final = "scope_mismatch"


def owner_check_failure(failure: ConnectionFailure, purpose: str) -> str | None:
    """The category of an owner check that proves nothing was written, else None (uncertain)."""
    if failure.category in NEVER_SENT:
        return failure.category
    status = failure.http_status
    if status is None or not 400 <= status <= 499:
        return None
    if status == 404:
        return MISSING[purpose]
    if status == 403:
        return "forbidden"
    return failure_of(failure).category


def write_failure(failure: ConnectionFailure, found: object) -> str | None:
    """``scope_mismatch`` for a 403 on an item write to a playlist the owner check read as
    public, else None (uncertain)."""
    if failure.http_status != 403 or not isinstance(found, Mapping):
        return None
    return NO_PUBLIC_SCOPE if cast(Mapping[str, Any], found).get("public") is True else None
