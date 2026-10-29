"""Retention registry (ADR 0007 section 11, guard 10): where provider-derived data is kept.

Amendment 1 and ADR 0010: nothing Spotify-derived is kept indefinitely; a user's own data goes
with the class or the account. Every owned table is listed with

- ``columns``: the columns that hold provider-derived or user-written content (titles, ids of the
  user's MOWL or Spotify objects, the brief, the conversation, ciphertext of provider tokens);
- ``reviewed``: the other ``text``, ``jsonb`` and ``bytea`` columns, looked at and found to hold
  none (enums, hashes, counters' labels, names of our own kinds);
- ``deleted_with``: how the rows go. ``class`` and ``account`` are ``ON DELETE CASCADE`` paths
  from ``classes`` and ``users`` that the guard checks in the catalog; ``mowl_connection`` is the
  application's own deletion when the MOWL connection is dropped or replaced (ADR 0006 section 3,
  ADR 0007 section 9), which the guard can't see and a test exercises.

``tests/retention_guard.py`` fails when an owned table is missing, when a ``text``, ``jsonb`` or
``bytea`` column of a listed table is in neither set, when an entry names a column or table that
does not exist, and when a ``class`` or ``account`` path isn't a cascade. Adding a column with
content therefore means deciding, in the same PR, how it is deleted.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

DeletionPath = Literal["class", "account", "mowl_connection"]


@dataclass(frozen=True)
class Retention:
    columns: frozenset[str]
    reviewed: frozenset[str]
    deleted_with: frozenset[DeletionPath]


def _entry(columns: str, reviewed: str, *deleted_with: DeletionPath) -> Retention:
    return Retention(
        frozenset(columns.split()), frozenset(reviewed.split()), frozenset(deleted_with)
    )


RETENTION: Mapping[str, Retention] = MappingProxyType(
    {
        "classes": _entry("title brief music_source", "", "class", "account"),
        "class_revisions": _entry(
            "content music provenance", "kind content_hash", "class", "account"
        ),
        "class_messages": _entry("body change", "role", "class", "account"),
        "saved_workouts": _entry(
            "name program_id playlist_id",
            "content_hash state_hash",
            "class",
            "account",
            "mowl_connection",
        ),
        "jobs": _entry(
            "input",
            "kind state stage idempotency_key request_hash leased_by approval_hash "
            "error_category credit_state",
            "class",
            "account",
        ),
        "job_events": _entry("payload", "kind", "class", "account"),
        "external_operations": _entry(
            "idempotency_key external_id result request_context",
            "kind request_hash state error_category resolved_by",
            "class",
            "account",
            "mowl_connection",
        ),
        "mowl_playlist_links": _entry(
            "source_id playlist_id", "source", "account", "mowl_connection"
        ),
        "agent_runs": _entry(
            "",
            "stage provider model model_answered effort prompt_version outcome job_kind "
            "price_version",
            "account",
        ),
        "connections": _entry(
            "public last_error secret secret_nonce wrapped_dek dek_nonce",
            "provider status key_id",
            "account",
            "mowl_connection",
        ),
    }
)
