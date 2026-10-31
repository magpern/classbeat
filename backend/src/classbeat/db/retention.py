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
  ADR 0007 section 9), which the guard can't see and a test exercises; ``spotify_connection`` is
  the same for Spotify (ADR 0009 section 7, ADR 0008 sections 2 and 8): Disconnect and an account
  change (``forget_spotify_account``), and the narrower ``forget_pool`` of a consent revoke, a
  ``pool_missing`` and a Client ID change, tested in ``tests/test_spotify_schema.py``.

``spotify_oauth_flows`` also go when used or expired (the callback, the hourly purge of
``classbeat.auth``); the registry lists the account, the path that always holds.

``tests/retention_guard.py`` fails when an owned table is missing, when a ``text``, ``jsonb`` or
``bytea`` column of a listed table is in neither set, when an entry names a column or table that
does not exist, and when a ``class`` or ``account`` path isn't a cascade. Adding a column with
content therefore means deciding, in the same PR, how it is deleted.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

DeletionPath = Literal["class", "account", "mowl_connection", "spotify_connection"]


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
        # ``content`` of a ``song_list`` (ADR 0008 sections 5 and 8) holds the chosen songs'
        # Spotify ids, titles, artists and, from ``v: 2``, their album cover URLs (``cover``, ADR
        # 0013 section 9): all go with the class and the account, like the drafts beside them.
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
            "spotify_connection",
        ),
        "mowl_playlist_links": _entry(
            "source_id playlist_id", "source", "account", "mowl_connection"
        ),
        "agent_runs": _entry(
            "",
            "stage provider model model_answered effort prompt_version outcome job_kind "
            "price_version route_key",
            "account",
        ),
        "connections": _entry(
            "public last_error secret secret_nonce wrapped_dek dek_nonce",
            "provider status key_id",
            "account",
            "mowl_connection",
            "spotify_connection",
        ),
        # ADR 0009 section 3: the user's own Client ID and the PKCE verifier (useless without the
        # one-time code); the redirect URI is classbeat's own callback.
        "spotify_oauth_flows": _entry(
            "client_id verifier", "state_hash session_hash redirect_uri", "account"
        ),
        # ADR 0008 section 8: the pool playlist id and the Spotify user id, with the consent.
        "spotify_pool": _entry(
            "spotify_user_id playlist_id", "last_hash", "account", "spotify_connection"
        ),
        # Final playlists: detached by Disconnect or an account change, then gone with the class.
        "class_playlists": _entry(
            "playlist_id spotify_user_id name",
            "state content_hash",
            "class",
            "account",
            "spotify_connection",
        ),
        # ADR 0011 section 1 (M5): plans, credits and their lock rows go with the account; none
        # holds Spotify-derived data. The note is the admin's own text about the grant.
        "entitlements": _entry("note", "plan_id source", "account"),
        "code_redemptions": _entry("", "", "account"),
        "credit_ledger": _entry("", "plan_id state reason", "account"),
        "quota_periods": _entry("", "", "account"),
    }
)
