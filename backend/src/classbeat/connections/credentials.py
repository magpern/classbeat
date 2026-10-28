"""What the provider library receives: a ``Credentials`` object, never a dict or bare fields.

ADR 0004 §3. ``Credentials`` is the library's own ``mowl.credentials.Credentials`` (mowl-workouts
v0.4.0), subclassed only to tighten what it shows: the library's ``repr`` hides the tokens but
still prints ``swedebeat_device_id``, the Spotify Client ID and the redirect URI, so here ``repr``
and ``str`` show nothing at all, and the object refuses to be pickled or copied by value. It is
still a ``mowl.credentials.Credentials`` (``isinstance`` holds), so every library call takes it.
"""

from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID

from mowl.credentials import Credentials as LibraryCredentials

from classbeat.connections.tables import PROVIDERS

# The secret fields of each provider's payload (canonical JSON, encrypted).
PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "mowl": frozenset({"token"}),
    "spotify": frozenset({"access_token", "refresh_token", "expires_at"}),
    "swedebeat": frozenset({"token", "device_id"}),
}
assert set(PAYLOAD_FIELDS) == set(PROVIDERS)  # noqa: S101 - a module invariant


class Credentials(LibraryCredentials):
    """The library's credentials. Never logged, stored or serialized."""

    def __repr__(self) -> str:
        return "Credentials(...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("credentials are never serialized")

    def __reduce_ex__(self, protocol: Any) -> NoReturn:
        raise TypeError("credentials are never serialized")

    def __copy__(self) -> NoReturn:
        raise TypeError("credentials are never copied")

    def __deepcopy__(self, memo: Any) -> NoReturn:
        raise TypeError("credentials are never copied")


def build_credentials(
    provider: str, payload: dict[str, Any], public: dict[str, Any]
) -> Credentials:
    """The provider's fields from its decrypted payload (and, for Spotify, the Client ID)."""
    if set(payload) != PAYLOAD_FIELDS[provider]:
        raise ValueError("the payload does not have the provider's fields")
    if provider == "mowl":
        return Credentials(mowl_token=str(payload["token"]))
    if provider == "swedebeat":
        return Credentials(
            swedebeat_token=str(payload["token"]), swedebeat_device_id=str(payload["device_id"])
        )
    return Credentials(
        spotify_client_id=str(public.get("client_id", "")),
        spotify_access_token=str(payload["access_token"]),
        spotify_refresh_token=str(payload["refresh_token"]),
        spotify_expires_at=float(payload["expires_at"]),
    )


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class OpenConnection:
    """A decrypted connection for one call: the row id, the provider, the credentials and the
    version that was read (``wrapped_dek``), so status changes apply only to that version."""

    id: UUID
    provider: str
    credentials: Credentials
    version: bytes

    def __repr__(self) -> str:
        return f"OpenConnection(id={self.id}, provider={self.provider!r}, credentials=...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("an open connection is never serialized")
