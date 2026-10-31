"""Provider connections (ADR 0004): the encrypted ``connections`` rows and the only path from a
row to the provider library's ``Credentials``.

Everything here takes a ``UserContext``. The library's exceptions never leave this package: each
call is wrapped and becomes a ``ConnectionFailure(category)`` with no chained exception (§5).
"""

from classbeat.connections.credentials import Credentials, OpenConnection
from classbeat.connections.errors import (
    CATEGORIES,
    ConnectionFailure,
    classify,
    run_library_call,
)
from classbeat.connections.repository import (
    ConnectionInfo,
    call_opened,
    connect,
    connection_refs,
    delete_all_in,
    disconnect,
    find,
    find_in,
    hold,
    list_connections,
    mark_needs_reconnect,
    open_connection,
    record_result,
    refs_in,
    save,
    use,
    vacuum_tables,
)
from classbeat.connections.rewrap import rewrap_owner, rows_by_key
from classbeat.connections.spotify_account import (
    SpotifyStatus,
    give_pool_consent,
    pool_consent_in,
    revoke_pool_consent,
    spotify_account_in,
    spotify_status,
)
from classbeat.connections.spotify_flows import (
    TakenFlow,
    create_flow,
    delete_own_flow,
    take_flow,
)
from classbeat.connections.spotify_tokens import RefreshedTokens, expires_at_of, refresh_spotify
from classbeat.connections.status import ConnectionStatus, connection_statuses
from classbeat.connections.tables import PROVIDERS

__all__ = [
    "CATEGORIES",
    "PROVIDERS",
    "ConnectionFailure",
    "ConnectionInfo",
    "ConnectionStatus",
    "Credentials",
    "OpenConnection",
    "RefreshedTokens",
    "SpotifyStatus",
    "TakenFlow",
    "call_opened",
    "classify",
    "connect",
    "connection_refs",
    "connection_statuses",
    "create_flow",
    "delete_all_in",
    "delete_own_flow",
    "disconnect",
    "expires_at_of",
    "find",
    "find_in",
    "give_pool_consent",
    "hold",
    "list_connections",
    "mark_needs_reconnect",
    "open_connection",
    "pool_consent_in",
    "record_result",
    "refresh_spotify",
    "refs_in",
    "revoke_pool_consent",
    "rewrap_owner",
    "rows_by_key",
    "run_library_call",
    "save",
    "spotify_account_in",
    "spotify_status",
    "take_flow",
    "use",
    "vacuum_tables",
]
