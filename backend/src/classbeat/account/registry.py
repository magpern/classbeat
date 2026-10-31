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

    def __post_init__(self) -> None:
        names = (self.key, self.table, *self.omit)
        if not all(_IDENT.match(name) for name in names) or not _ORDER.match(self.order):
            raise ValueError(f"unsafe name in the export registry: {self.table}")


def _section(table: str, order: str = "created_at, id", omit: str = "") -> Section:
    return Section(table, table, order, frozenset(omit.split()))


SECTIONS: tuple[Section, ...] = (
    _section("classes"),
    _section("class_revisions", "class_id, number", "content_hash"),
    _section("class_messages", "class_id, seq"),
    _section("saved_workouts", omit="content_hash state_hash"),
    _section("class_playlists", omit="content_hash"),
    _section("mowl_playlist_links"),
    _section("spotify_pool", "consented_at, id", "last_hash"),
    _section(
        "jobs",
        omit="idempotency_key request_hash leased_by lease_version lease_expires_at "
        "heartbeat_at approval_hash next_seq",
    ),
    _section("job_events", "job_id, seq", "lease_version"),
    _section(
        "external_operations",
        omit="idempotency_key request_hash lease_version resolved_by",
    ),
    _section("agent_runs", "started_at, id", "eval_result_id"),
    _section("entitlements", omit="granted_by revoked_by"),
    _section("code_redemptions"),
    _section("credit_ledger", omit="refunded_by"),
    _section("quota_periods", "period, id"),
)

NOT_EXPORTED: Mapping[str, str] = MappingProxyType(
    {
        "connections": "exported as a status per provider, never the row: it holds the "
        "encrypted tokens, their wrapped keys and the Spotify Client ID",
        "spotify_oauth_flows": "an unfinished connect: a one-time PKCE verifier and state hash, "
        "deleted when used or expired, nothing the user wrote",
    }
)
