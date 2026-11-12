"""What the export says about each owned table (ADR 0014 section 2).

Every owned table has exactly one entry: a ``Section`` (exported as rows, minus the columns
listed in ``omit``) or a reason in ``NOT_EXPORTED``. ``tests/test_account_export_registry.py``
reads the live schema and fails when an owned table is in neither, when an entry names a table
or column that does not exist, and when a ``bytea`` column of an exported table is not omitted:
hashes, keys and ciphertext never go into an export, and a new one must be decided in the PR
that adds the column, as for the retention registry (guard 10).

``omit`` holds internals the user has no use for and that identify no data of theirs: idempotency
keys, request hashes, lease and worker bookkeeping, and the ids of the admins who acted on a row.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_ORDER = re.compile(r"^[a-z_][a-z0-9_]*(, [a-z_][a-z0-9_]*)*$")


@dataclass(frozen=True, slots=True)
class Section:
    key: str  # the name in the export file
    table: str
    order: str  # a fixed ORDER BY list, so the file is the same twice
    omit: frozenset[str]
    # Non-empty: the section reads exactly these columns, for a table whose app grant is a column
    # list (the price columns of ``billing_events`` and ``subscriptions``, ADR 0031 section 3);
    # ``omit`` then stays empty. ``test_account_export_registry`` checks each is SELECTable.
    columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        names = (self.key, self.table, *self.omit, *self.columns)
        if not all(_IDENT.match(name) for name in names) or not _ORDER.match(self.order):
            raise ValueError(f"unsafe name in the export registry: {self.table}")


def _section(
    table: str, order: str = "created_at, id", omit: str = "", columns: str = ""
) -> Section:
    return Section(table, table, order, frozenset(omit.split()), tuple(columns.split()))


SECTIONS: tuple[Section, ...] = (
    _section("classes"),
    _section("class_revisions", "class_id, number", "content_hash"),
    _section("class_messages", "class_id, seq"),
    # ADR 0007 amendment "beat-one corrections": the fingerprint is bookkeeping, already in the
    # exported revision's ``music``.
    _section("beat_one_corrections", "class_id, song, id", "fingerprint"),
    _section("saved_workouts", omit="content_hash state_hash"),
    _section("class_playlists", omit="content_hash"),
    _section("mowl_playlist_links"),
    _section("spotify_pool", "consented_at, id", "last_hash"),
    _section(
        "jobs",
        omit="idempotency_key request_hash leased_by lease_version lease_expires_at "
        "heartbeat_at approval_hash next_seq analysis_slot fresh_analysis analysis_fetch_ms "
        "analysis_decode_ms analysed_at",
    ),
    _section("job_events", "job_id, seq", "lease_version"),
    _section(
        "external_operations",
        omit="idempotency_key request_hash lease_version resolved_by",
    ),
    _section("agent_runs", "started_at, id", "eval_result_id"),
    _section("entitlements", omit="granted_by revoked_by"),
    # ADR 0031 section 3 (#948): the app role cannot read the amount or the currency, so these
    # two list their columns. The Dummy ``event_id`` is a digest: nothing of an idempotency key.
    _section(
        "billing_events",
        columns="id owner_id provider event_id kind plan_id period_start period_end outcome "
        "created_at",
    ),
    _section(
        "subscriptions",
        columns="id owner_id provider plan_id anchor_day period_start period_end next_plan_id "
        "cancel_at_end status last_event_at created_at updated_at",
    ),
    _section("billing_log"),
    # ADR 0032 section 3 (#973): the payment provider's ids of the user's own checkouts.
    _section("billing_links"),
    _section("code_redemptions"),
    _section("credit_ledger", omit="refunded_by"),
    _section("quota_periods", "period, id"),
    # Issue #525: the user's own preferences ("I use Swedebeat"); at most one row.
    _section("user_preferences"),
    # Issue #632: the user's own requests of the last days (method, route template, status,
    # time). The network's keyed hash is never exported (bytea); the session id and the 429 row's
    # minute are internal.
    _section("security_log", "at, id", "ip_hash session_id limited_minute"),
)

NOT_EXPORTED: Mapping[str, str] = MappingProxyType(
    {
        "connections": "exported as a status per provider, never the row: it holds the "
        "encrypted tokens, their wrapped keys and the Spotify Client ID",
        "swedebeat_mix_maps": "exported as a count only (``swedebeat_mix_analyses`` in the file, "
        "ADR 0016 section 3): the analysis of a licensed mix is a derived map, not something the "
        "user wrote",
        "spotify_oauth_flows": "an unfinished connect: a one-time PKCE verifier and state hash, "
        "deleted when used or expired, nothing the user wrote",
        "swedebeat_analyses": "the times new mix analyses started, for the daily limit (ADR 0016 "
        "section 7): no content, gone after two days, nothing the user wrote",
        "mail_handoff": "a 'class ready' mail on its way from the worker to the web process (ADR "
        "0022 section 4): the class id only, taken within seconds and gone after a day at most, "
        "nothing the user wrote",
        "swedebeat_handoffs": "exported as a count only (``swedebeat_handoffs`` in the file, "
        "ADR 0016 section 7): the snapshot of mix ids and the verdict are the step's bookkeeping",
    }
)
