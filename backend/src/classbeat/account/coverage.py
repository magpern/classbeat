"""Every column that refers to a user, and what deleting the user does to it (ADR 0014 section 3).

``USER_REFERENCES`` lists the foreign keys to ``users`` (``c`` = ``ON DELETE CASCADE``: the row
goes with the account; ``n`` = ``ON DELETE SET NULL``: the row stays and loses the link), and
``UNLINKED_IDS`` the ``uuid`` columns that name a user without a foreign key (they keep a bare id
once the account is gone, which identifies nobody). ``RETAINED`` is the one place that keeps
personal data after the account is deleted, and why.

``tests/gdpr_guard.py`` reads the live schema (``pg_constraint`` and ``information_schema``) and
fails when a foreign key to ``users`` or a user-looking ``uuid`` column is not listed here, when an
entry is not in the schema, or when its action differs from the database's. So a new table or
column that refers to a user cannot pass CI without a decision here, in the PR that adds it.
"""

from collections.abc import Mapping
from types import MappingProxyType

CASCADE = "c"
SET_NULL = "n"

_OWNED = (
    "agent_runs class_messages class_playlists class_revisions classes code_redemptions "
    "connections credit_ledger entitlements external_operations job_events jobs "
    "mowl_playlist_links quota_periods saved_workouts spotify_oauth_flows spotify_pool "
    "swedebeat_mix_maps"
)
_ACCOUNT = (
    "auth_events auth_tokens identities known_devices mfa_recovery_codes mfa_totp "
    "oauth_flows sessions"
)

USER_REFERENCES: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        **{(table, "owner_id"): CASCADE for table in _OWNED.split()},
        **{(table, "user_id"): CASCADE for table in _ACCOUNT.split()},
        # Who created, used, granted or changed: the row is the operator's or the invite's, not
        # the user's, and keeps no personal data once the id is null.
        ("invites", "created_by"): SET_NULL,
        ("invites", "used_by"): SET_NULL,
        ("plans", "updated_by"): SET_NULL,
        ("redeem_codes", "created_by"): SET_NULL,
        ("entitlements", "granted_by"): SET_NULL,
        # The admin audit log is append-only (ADR 0011 section 5); see ``RETAINED``.
        ("admin_audit", "actor_id"): SET_NULL,
        ("admin_audit", "target_user_id"): SET_NULL,
    }
)

# A user id with no foreign key: an admin who revoked, refunded or changed something. After the
# admin's account is deleted it is a bare UUID that leads to nobody.
UNLINKED_IDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("entitlements", "revoked_by"),
        ("credit_ledger", "refunded_by"),
        ("app_settings", "updated_by"),
    }
)

# Personal data that outlives the account, by design (ADR 0014 section 5).
RETAINED: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("admin_audit", "target_id"): "the bare id of the user an operator acted on, as text "
        "('user.delete', 'user.disable'): the log cannot be changed (T92), and once the account "
        "is gone nothing in the database maps the id to a person; it is kept as long as the log",
        ("admin_audit", "actor_email"): "the append-only log of what an operator did (ADR 0011 "
        "section 5, T92): an admin's address stays beside their actions after their account is "
        "deleted, for accountability; a user who never acted as an admin has no such row",
    }
)

# A ``uuid`` column whose name matches this reads as a reference to a user, so it must be in
# ``USER_REFERENCES`` or ``UNLINKED_IDS`` (checked against the schema by the guard).
USER_COLUMN_PATTERN = r"^(.*_by|.*user_id|owner_id|actor_id)$"
