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
    # ADR 0009 section 6 step 2: the library gets the access token only. No refresh token and
    # renewal off, so it never contacts the token endpoint and never persists anything (the
    # refresh is ``classbeat.connections.spotify_tokens``'s, under the row lock); no sleeping on
    # a 429 either (the job's backoff honours ``Retry-After``, section 8).
    return Credentials(
        spotify_client_id=str(public.get("client_id", "")),
        spotify_access_token=str(payload["access_token"]),
        spotify_refresh_token="",
        spotify_expires_at=float(payload["expires_at"]),
        spotify_refresh_enabled=False,
        spotify_max_retry_wait=0.0,
    )


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class OpenConnection:
    """A decrypted connection for one call: the row id, the provider, the credentials and the
    versions that were read, so status changes apply only to that version.

    ``generation`` (ADR 0009 section 5.4) is the connection the user made: ``hold``,
    ``record_result`` and the flips compare it. ``version`` is the ``wrapped_dek`` read, which
    every write of the row (a refresh, a rewrap, a reconnect) changes: a Spotify flip compares it
    as well (section 6), so a 401 sent with a token that is no longer the row's flips nothing."""

    id: UUID
    provider: str
    credentials: Credentials
    version: bytes
    generation: int
    # The provider account the row was connected as, where the row records one: the Spotify
    # user id of ``public`` (ADR 0009 section 5). ``hold`` compares it, and the Spotify writes
    # check playlist ownership against it (ADR 0008 section 2). None for MOWL and Swedebeat.
    account: str | None = None

    def __repr__(self) -> str:
        return f"OpenConnection(id={self.id}, provider={self.provider!r}, credentials=...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("an open connection is never serialized")
