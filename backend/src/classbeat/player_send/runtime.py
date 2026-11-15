"""What the worker gives "Send to player" and the removal (ADR 0034 section 5): the key ring
(to open the player connection's token) and the player's client factory.

Only the worker configures it (``classbeat.worker.__main__``), after it installed the player's
host. In the web process it stays unset, so **Check again** on a player operation there is
inconclusive: the web process has no client that reaches the player.
"""

from dataclasses import dataclass, field

from classbeat import egress
from classbeat.crypto.keks import KeyRing
from classbeat.player_send.source_calls import ClientFactory


@dataclass(frozen=True)
class Runtime:
    keyring: KeyRing
    client_factory: ClientFactory = field(default=egress.player_async_client)


_runtime: Runtime | None = None


def configure(runtime: Runtime | None) -> None:
    """Set (or clear, with None) the worker's runtime."""
    global _runtime
    _runtime = runtime


def current_runtime() -> Runtime | None:
    return _runtime
