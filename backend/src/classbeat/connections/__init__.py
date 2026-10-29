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
from classbeat.connections.tables import PROVIDERS

__all__ = [
    "CATEGORIES",
    "PROVIDERS",
    "ConnectionFailure",
    "ConnectionInfo",
    "Credentials",
    "OpenConnection",
    "call_opened",
    "classify",
    "connect",
    "connection_refs",
    "delete_all_in",
    "disconnect",
    "find",
    "find_in",
    "hold",
    "list_connections",
    "mark_needs_reconnect",
    "open_connection",
    "record_result",
    "refs_in",
    "rewrap_owner",
    "rows_by_key",
    "run_library_call",
    "save",
    "use",
    "vacuum_tables",
]
