"""What the two Spotify writes share (ADR 0008 section 2, ADR 0009 sections 6 and 11): the runtime,
the track-list hash, the owner check and the read-back, and the one way they call Spotify.

- **By id, never by name.** Every playlist id written to comes from a row classbeat stored (the
  pool row, a ``class_playlists`` row, or the create's own answer) and is validated as 22 letters
  and digits before it goes into a path; URIs are rebuilt from validated track ids.
- **The owner check** (``owned``): ``GET /playlists/{id}`` must name the connected user as the
  owner, with ``public`` and ``collaborative`` exactly ``false``.
- **The read-back** (``observed_ids``): every entry of the playlist in order; anything that is
  not a playable track with a valid id is a marker (``None``), so a playlist holding it never
  equals a list classbeat wrote.
- ``call`` runs one ``mowl.spotify_web`` function through ``classbeat.spotify.spotify_call``
  with ``expect`` set to the connection the operation's decide step held: if the row is
  reconnected, changed to another Client ID or account in between, nothing is sent. A
  multi-request write passes its ``deadline_at``, so no request of it is sent after
  ``pending_at + bound`` (``classbeat.egress.call_deadline_until``).

Nothing here logs a playlist id, a track id or a provider text.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from mowl import spotify_web

from classbeat import egress
from classbeat.connections import Credentials, OpenConnection
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure
from classbeat.operations.kinds import Factory
from classbeat.spotify.access import DEFAULT_REFRESH_MARGIN_SECONDS, spotify_call
from classbeat.spotify.http import SpotifyHttp

SPOTIFY_ID: Final = re.compile(r"[0-9A-Za-z]{22}")
# The total deadline of one request (the library's ``urlopen`` timeout; the egress watchdog makes
# it a total deadline). A bound counts one of these per request.
REQUEST_SECONDS: Final = 30.0
MAX_TRACKS: Final = spotify_web.MAX_ITEMS  # a read over it fails rather than truncating
WRITE_CHUNK: Final = 100  # ``PUT``/``POST /items`` take at most 100 URIs
READ_PAGE: Final = spotify_web.PAGE  # ``GET /items`` pages of 50
MAX_LIST_PAGES: Final = spotify_web.MAX_LIST_PAGES  # the reconciler's listing: 20 pages of 50
POOL_NAME: Final = "classbeat working copy"  # ADR 0009 section 9's wording
MAX_NAME: Final = 100
MAX_SNAPSHOT: Final = 200


@dataclass(frozen=True, slots=True)
class Runtime:
    """What the process gives the Spotify writes: the token client for a refresh (the worker's
    and the web app's ``SpotifyHttp``; None builds the default one), the refresh margin and
    ``CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS`` (None: a listing without the playlist proves
    nothing)."""

    http: SpotifyHttp | None = None
    margin_seconds: float = DEFAULT_REFRESH_MARGIN_SECONDS
    listing_lag_seconds: float | None = None


_runtime = Runtime()


def configure(runtime: Runtime) -> None:
    """Set the runtime (the worker and the web app at start; tests)."""
    global _runtime
    lag = runtime.listing_lag_seconds
    if lag is not None and not (math.isfinite(lag) and lag > 0):
        raise ValueError("the listing lag is a positive number of seconds")
    _runtime = runtime


def runtime() -> Runtime:
    return _runtime


def check_playlist_id(value: object) -> str:
    """A playlist id from our own rows: 22 letters and digits, or the write is refused."""
    if not isinstance(value, str) or SPOTIFY_ID.fullmatch(value) is None:
        raise JobFailure("invalid_input")
    return value


def check_track_ids(track_ids: Iterable[object]) -> tuple[str, ...]:
    """The ordered track ids of a write: each 22 letters and digits, at most ``MAX_TRACKS``."""
    ids = tuple(track_ids)
    if len(ids) > MAX_TRACKS:
        raise JobFailure("invalid_input")
    for value in ids:
        if not isinstance(value, str) or SPOTIFY_ID.fullmatch(value) is None:
            raise JobFailure("invalid_input")
    return cast(tuple[str, ...], ids)


def check_name(name: object) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME or not name.isprintable():
        raise JobFailure("invalid_input")
    return name


def ids_hash(track_ids: Sequence[str]) -> bytes:
    """SHA-256 of the ordered track ids as canonical JSON: what a replace writes, what
    ``spotify_pool.last_hash`` and ``class_playlists.content_hash`` record."""
    return hashlib.sha256(
        json.dumps(list(track_ids), separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).digest()


def track_uris(track_ids: Sequence[str]) -> list[str]:
    return [f"spotify:track:{track_id}" for track_id in track_ids]


def description(ref: str, purpose: str) -> str:
    """The new playlist's description: the operation's ``ref`` (what the reconciler looks for),
    and for the pool one fixed sentence."""
    text = f"classbeat ref {ref}"
    if purpose == "pool":
        text += ". classbeat's working playlist for song picking; it is overwritten on each pick."
    return text


_REF = re.compile(r"classbeat ref ([0-9a-f]{12})(?![0-9A-Za-z])")


def refs_in(text: object) -> set[str]:
    """Every ``ref`` a playlist's description names."""
    return set(_REF.findall(text)) if isinstance(text, str) else set()


def owned(found: object, playlist_id: str, account: str | None) -> bool:
    """The owner check of ADR 0008 section 2: this playlist, owned by the connected user, not
    public and not collaborative (both exactly ``false``)."""
    if account is None or not isinstance(found, Mapping):
        return False
    playlist = cast(Mapping[str, Any], found)
    return (
        playlist.get("id") == playlist_id
        and playlist.get("owner_id") == account
        and playlist.get("public") is False
        and playlist.get("collaborative") is False
    )


def observed_ids(entries: object) -> list[str | None] | None:
    """The read-back: each entry's track id in order, ``None`` for a marker (null, an episode, a
    local file, no valid id). None when the answer is not a list of entries."""
    if not isinstance(entries, list):
        return None
    out: list[str | None] = []
    for entry in cast(list[Any], entries):
        if not isinstance(entry, Mapping):
            return None
        item = cast(Mapping[str, Any], entry)
        track_id = item.get("id")
        playable = item.get("playable") is True and item.get("type") == "track"
        valid = isinstance(track_id, str) and SPOTIFY_ID.fullmatch(track_id) is not None
        out.append(track_id if playable and valid else None)  # pyright: ignore[reportUnknownArgumentType]
    return out


def snapshot_of(answer: object) -> str | None:
    return answer if isinstance(answer, str) and 0 < len(answer) <= MAX_SNAPSHOT else None


async def call[T](
    factory: Factory,
    keyring: KeyRing,
    ctx: UserContext,
    opened: OpenConnection,
    fn: Callable[[Credentials], T],
    *,
    write: bool = False,
    single_request: bool = False,
    deadline_at: float | None = None,
) -> T:
    """``fn(credentials)`` through ``spotify_call`` for the connection ``opened`` (and only it)."""

    def bounded(creds: Credentials) -> T:
        if deadline_at is None:
            return fn(creds)
        with egress.call_deadline_until(deadline_at):
            return fn(creds)

    current = _runtime
    return await spotify_call(
        factory,
        keyring,
        ctx,
        bounded,
        http=current.http,
        write=write,
        margin_seconds=current.margin_seconds,
        expect=opened,
        single_request=single_request,
    )
