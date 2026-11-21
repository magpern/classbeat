"""Which spotdown ``cache_scope`` a source kind is analysed in (ADR 0040 section 7 and its
amendment of 2026-11-21; owner decision #1297, #1338). Pure: no I/O.

An **allowlist**, so a new kind is never shared by mistake:

- ``studio``: a SwedeBeat studio track is the same catalog file for every user (the same sha256),
  so its analysis is shared by every owner, like a YouTube analysis: the one ``shared_scope`` of
  the kind, which names no owner. spotdown requires a scope for an upload and for the sha256
  lookup (contract v2, ``422 scope_required``), so "global" for classbeat is this one scope, and
  spotdown stays unchanged.
- ``mix`` (a SwedeBeat mix) and ``upload`` (a user's own audio, not planned; it would need its own
  decision): the owner's ``cache_scope``, an HMAC of the owner id, so they never cross users and
  account deletion removes them (``DELETE /v2/scopes/{scope}``, ``scope_outbox``).
- any other kind: ``ScopeRefused``, so nothing is sent.
"""

from typing import Final
from uuid import UUID

from classbeat.spotdown.scope_calc import cache_scope, shared_scope

SHARED_KINDS: Final = frozenset({"studio"})
OWNER_KINDS: Final = frozenset({"mix", "upload"})


class ScopeRefused(ValueError):
    """A source kind the allowlist does not name; the message never holds the value."""


def scope_for(kind: str, key: bytes, owner_id: UUID) -> str:
    """The ``cache_scope`` an analysis of ``kind`` for ``owner_id`` is looked up and sent in."""
    if kind in SHARED_KINDS:
        return shared_scope(key, kind)
    if kind in OWNER_KINDS:
        return cache_scope(key, owner_id)
    raise ScopeRefused("a source kind the scope policy does not name")
