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
        "classes": _entry(
            "title brief music_source interpretation", "dismissed_rules", "class", "account"
        ),
        # ``content`` of a ``song_list`` (ADR 0008 sections 5 and 8) holds the chosen songs'
        # Spotify ids, titles, artists and, from ``v: 2``, their album cover URLs (``cover``, ADR
        # 0013 section 9): all go with the class and the account, like the drafts beside them.
        # Each kind the table allows is in ``REVISION_KINDS`` below.
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
            "error_category credit_state spotify_failure",
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
        # ADR 0016 section 3: the user's own analysis of a mix Swedebeat licensed to them (the mix
        # name, MOWL's playlist id, the map). No audio, nothing Spotify-derived. At most 20 per user
        # and 30 days (A3); gone with the account and with the MOWL connection (Disconnect, an
        # account change, ``connections.repository._forget_mowl_account``).
        "swedebeat_mix_maps": _entry(
            "mix_id name playlist_id map", "", "account", "mowl_connection"
        ),
        # ADR 0016 section 7 (A4): when each new mix analysis started, for the daily limit. No
        # content; a user's rows past 48 hours go with their next one, all with the account.
        "swedebeat_analyses": _entry("", "", "account"),
        # ADR 0016 section 7 (B2): the manual step. Mix ids only (the snapshot and the verified
        # mix). Gone with the class, the account, and the MOWL connection (Disconnect and an
        # account change). Rows expired for 7 days go in the hourly purge.
        "swedebeat_handoffs": _entry(
            "mixes_before mix_id detail",
            "state verdict",
            "class",
            "account",
            "mowl_connection",
        ),
        # Issue #525 (migration 0028): the user's own switches, booleans and dates only; nothing
        # provider-derived. Gone with the account; the one row per user is the whole record.
        "user_preferences": _entry("", "", "account"),
        # Issue #632 (migration 0034): the security log. A keyed hash of the coarse network, the
        # method, the route template and the status; no content, nothing provider-derived. Gone
        # with the account; rows older than ``CLASSBEAT_SECURITY_LOG_DAYS`` go in the hourly purge.
        "security_log": _entry("", "ip_hash method route", "account"),
        # ADR 0022 section 4 (ME5, migration 0037): a "class ready" mail the worker hands to the
        # web process. The class id only (``params``), no address, title or token; nothing
        # provider-derived. Gone with its job (and so its class) and the account; taking a row
        # deletes it, and ``mail_handoff_take`` drops rows older than a day (also with SMTP unset,
        # the web process's hourly purge-only call).
        "mail_handoff": _entry("", "kind params", "class", "account"),
        # ADR 0007 amendment "beat-one corrections" (#535): a song number, a whole-beat shift and
        # the song's BPM per class and music fingerprint (a hash of the user's own music); no title,
        # no id.
        "beat_one_corrections": _entry("", "fingerprint", "class", "account"),
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
        # ADR 0031 section 3 (M7a, migration 0046): the subscription, the provider's events and
        # the user's billing log. Codes, plan ids and timestamps; nothing provider-derived in the
        # Spotify sense, and the Dummy ``event_id`` is a digest. All go with the account.
        "billing_events": _entry("", "provider event_id kind plan_id currency outcome", "account"),
        "subscriptions": _entry(
            "", "provider plan_id basis_currency next_plan_id status", "account"
        ),
        "billing_log": _entry("", "action outcome reason request_id", "account"),
    }
)

# Every ``class_revisions.kind`` (the table's check), with what its ``content`` and ``music`` hold.
# All go with the class and the account (the table's entry above); none outlives its class. A new
# kind is added here in the PR that allows it (``tests/test_swedebeat_match_retention.py`` compares
# this with the check).
REVISION_KINDS: Mapping[str, str] = MappingProxyType(
    {
        "draft": "the user's brief's design; MOWL playlist ids and track ids of its music",
        "song_list": "Spotify-derived: the chosen songs' ids, titles, artists and cover URLs",
        # ADR 0016 section 5 (B1): the match of one ``song_list`` against Swedebeat's catalogue.
        # Spotify-derived (each song's title and artist) and Swedebeat's (the matched title,
        # artist and length); no catalogue id, no URL, no token. Never cached across classes or
        # users: the job dedupes in memory only.
        "swedebeat_match": "Spotify-derived titles and artists; Swedebeat titles, artists, lengths",
    }
)
