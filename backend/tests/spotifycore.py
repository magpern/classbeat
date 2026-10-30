"""The fake Spotify: its state and its answers (ADR 0009 §11, §13). TEST ONLY, never the network.

Standard library only, because two adapters share it: ``tests/spotifyfake.py`` (in-process: a
urllib network handler and an httpx transport) and ``e2e/fake-spotify/server.py`` (answers as
``api.spotify.com`` and ``accounts.spotify.com`` inside the compose network, which mounts this
file). ``FakeSpotify.handle(method, url, headers, body)`` is the whole fake; the adapters only
carry bytes. Public test values only; nothing here is a real app, account or token.

What it encodes, from ADR 0009 (assumptions to verify at the first live run, ADR 0008 §11):

- **Apps** by Client ID (``add_app``): their registered redirect URIs, allow-list (Development
  Mode), whether the owner has Premium, the scope they grant, whether refresh tokens rotate. A
  user missing from the allow-list (or an owner without Premium) gets a 403 on every API call,
  the first being ``GET /me`` at connect.
- **Users** (``add_user``) are Spotify accounts; their playlists belong to the account, not to
  an app (a second Client ID of the same user sees the same playlists, as Spotify does).
  ``sign_in(user)`` is who approves at the authorize page.
- **Authorize** (``GET accounts/authorize``): checks the Client ID and the redirect URI (an
  unknown one is an error page, no redirect), then answers the 302 to the redirect URI with a
  one-time ``code`` and the ``state`` as given, or ``error=access_denied`` after ``deny()``.
- **Token** (``POST accounts/api/token``): ``authorization_code`` with PKCE (S256 of the
  ``code_verifier`` must equal the challenge; the code is single-use, bound to its Client ID and
  redirect URI, 10 minutes), ``refresh_token`` with rotation (the old refresh token dies) or
  without (no ``refresh_token`` in the answer). A ``client_secret``, in the form or as Basic
  ``Authorization``, is recorded and refused: nothing in classbeat may send one.
- **API**: ``GET /v1/me``; ``GET /v1/search`` over an invented catalogue (``limit`` at most 10);
  ``POST /v1/me/playlists`` (``public`` defaults to true when omitted, as Spotify's does);
  ``GET /v1/playlists/{id}`` (``fields`` is recorded, not applied: readers ignore extra keys);
  ``GET``, ``PUT`` and ``POST /v1/playlists/{id}/items`` (pages of at most 50, writes of at most
  100 URIs; entries name the track ``item``, or the legacy ``track`` with ``item_shape``);
  ``GET /v1/me/playlists`` (pages of at most 50). ``/tracks`` is gone (404). Answers carry
  ``next``, ``href``, ``uri`` and ``external_urls`` like Spotify's, so tests can show none is
  followed. Another account's playlist answers 404 (``foreign = "404"``, the default, strictest
  for isolation tests) or its metadata with 403 on its contents and writes (``"visible"``).
- **Failure injection** (``inject``) by request number (1-based, every request counted, both
  hosts) and/or method and path, ``times`` requests long. See ``KINDS``.
- **Records** every request (``seen``): its number, method, host, path, the ``Authorization``
  header and the ``client_id`` it carried, the app and user its bearer token belongs to, whether
  it carried a client secret, and the outcome. Tests assert from it that nothing crosses users.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

API_HOST = "api.spotify.com"
ACCOUNTS_HOST = "accounts.spotify.com"
API = f"https://{API_HOST}/v1"
SCOPES = "playlist-read-private playlist-modify-private"
SEARCH_LIMIT = 10
PAGE_LIMIT = 50
WRITE_LIMIT = 100
ACCESS_SECONDS = 3600
CODE_SECONDS = 600
# A host on no allowlist: the ``next_unlisted`` injection points ``next`` at it.
UNLISTED_HOST = "next.unlisted.example"
NOT_ALLOWLISTED = (
    "Check settings on developer.spotify.com/dashboard, the user may not be registered."
)
ID = re.compile(r"[0-9A-Za-z]{22}")
TRACK_URI = re.compile(r"spotify:track:([0-9A-Za-z]{22})")
EPISODE_URI = re.compile(r"spotify:episode:([0-9A-Za-z]{22})")
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
CLIENT_ID = re.compile(r"[0-9a-f]{32}")

# What ``inject`` can do. ``refuse``: nothing applied, the connection fails before anything is
# sent. ``drop``: applied, then the connection closes without an answer. ``noid``: applied,
# answered without ``id`` and ``snapshot_id``. ``invalid_grant``, ``invalid_client``: the token
# endpoint's 400 with that OAuth error code. ``status``: an API error with ``status`` (401, 403
# with ``message``, 404, 5xx). ``rate_limited``: 429 with ``Retry-After: retry_after``.
# ``trickle``: the answer arrives one byte per ``delay`` seconds. ``next_unlisted``: a page whose
# ``next`` points at ``UNLISTED_HOST``. ``local``, ``episode``, ``null``, ``duplicate``: a page of
# items gets a local file, a podcast episode, a null item or a copy of its first entry in front.
# ``partial``: a write applies only its first ``keep`` URIs and answers ``status`` (default 200).
KINDS = frozenset(
    {
        "refuse",
        "drop",
        "noid",
        "invalid_grant",
        "invalid_client",
        "status",
        "rate_limited",
        "trickle",
        "next_unlisted",
        "local",
        "episode",
        "null",
        "duplicate",
        "partial",
    }
)
_ANSWER_ONLY = frozenset({"invalid_grant", "invalid_client", "status", "rate_limited"})
_PAGE_KINDS = frozenset({"next_unlisted", "local", "episode", "null", "duplicate"})


@dataclass
class Answer:
    """What the adapter does: answer, or ``refuse`` (fail before sending, nothing applied) or
    ``drop`` (the effect applied, the connection closed without an answer). ``trickle`` is the
    pause between bytes of the body."""

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    refuse: bool = False
    drop: bool = False
    trickle: float = 0.0


@dataclass
class Seen:
    number: int
    method: str
    host: str
    path: str
    query: dict[str, str]
    authorization: str | None  # the header as sent
    client_id: str | None  # the ``client_id`` the request carried (token form, authorize query)
    token_app: str | None = None  # the Client ID the bearer token was issued to
    token_user: str | None = None  # the user it was issued for
    client_secret: bool = False
    outcome: str = "answered"  # answered, refused, dropped
    status: int | None = None


@dataclass
class Injection:
    kind: str
    at: int | None = None
    method: str | None = None
    path: str | None = None  # a regular expression the whole path must match
    times: int = 1
    status: int | None = None
    retry_after: int | None = None
    message: str | None = None
    keep: int | None = None
    delay: float = 0.05

    def matches(self, number: int, method: str, path: str) -> bool:
        return (
            self.times > 0
            and (self.at is None or self.at == number)
            and (self.method is None or self.method.upper() == method)
            and (self.path is None or re.fullmatch(self.path, path) is not None)
        )


@dataclass
class App:
    client_id: str
    redirect_uris: set[str]
    allowed: set[str]
    owner_premium: bool = True
    grant_scope: str | None = None  # None: the scope asked for
    rotate_refresh: bool = True


@dataclass
class Code:
    client_id: str
    user: str
    redirect_uri: str
    challenge: str
    scope: str
    expires_at: float


@dataclass
class Grant:
    client_id: str
    user: str
    scope: str
    expires_at: float = 0.0


@dataclass
class Playlist:
    id: str
    owner: str
    name: str
    description: str
    public: bool
    collaborative: bool
    entries: list[str | None]  # track or episode URIs, ``spotify:local:...``, None (a null item)
    collaborators: set[str] = field(default_factory=lambda: set[str]())
    version: int = 1

    @property
    def snapshot_id(self) -> str:
        return f"snap-{self.id}-{self.version}"


def challenge_of(verifier: str) -> str:
    """The S256 code challenge of a PKCE verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


_WORDS = [
    "Climb",
    "Summit",
    "Ridge",
    "Pulse",
    "Tempo",
    "Sprint",
    "Valley",
    "Thunder",
    "Ember",
    "Harbor",
    "Neon",
    "Cascade",
    "Drift",
    "Horizon",
    "Velvet",
    "Comet",
    "Granite",
    "Willow",
    "Signal",
    "Orbit",
]
_ARTISTS = (
    "The Invented Band",
    "Placeholder Collective",
    "Test Pattern",
    "Nobody Real",
    "Fixture Five",
    "Mock Orchestra",
)
CATALOGUE_SIZE = 160


def track_id(i: int) -> str:
    return f"FakeTrack{i:013d}"  # 22 letters and digits


def _catalogue() -> dict[str, dict[str, Any]]:
    tracks: dict[str, dict[str, Any]] = {}
    for i in range(CATALOGUE_SIZE):
        tid = track_id(i)
        artist = _ARTISTS[i % len(_ARTISTS)]
        tracks[tid] = {
            "id": tid,
            "name": f"{_WORDS[i % len(_WORDS)]} {_WORDS[(i * 7 + 3) % len(_WORDS)]} {i}",
            "artist": artist,
            "artist_id": f"FakeArtist{_ARTISTS.index(artist):012d}",
            "album": f"Invented Album {i // 10}",
            "album_id": f"FakeAlbum{i // 10:013d}",
            "release_date": f"{2000 + i % 25}-0{1 + i % 9}-1{i % 10}",
            "duration_ms": 150_000 + (i * 7919) % 150_000,
        }
    return tracks


class FakeSpotify:
    """One fake Spotify, both hosts. Thread-safe: every request runs under one lock."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.apps: dict[str, App] = {}
        self.users: dict[str, str] = {}  # id -> display name
        self.signed_in: str | None = None
        self.denying = False
        self.codes: dict[str, Code] = {}
        self.access: dict[str, Grant] = {}
        self.refresh: dict[str, Grant] = {}
        self.playlists: dict[str, Playlist] = {}
        self.catalogue = _catalogue()
        self.seen: list[Seen] = []
        self.injections: list[Injection] = []
        self.item_shape = "item"  # or "track", the legacy name of an entry's track
        self.foreign = "404"  # or "visible"
        self._count = 0
        self._next_playlist = 0

    # --- set-up ----------------------------------------------------------------------------

    def add_user(self, user: str, display_name: str | None = None) -> str:
        if not 1 <= len(user) <= 100 or not user.isprintable():
            raise ValueError("a Spotify user id is 1 to 100 printable characters")
        self.users[user] = display_name or user
        return user

    def add_app(
        self,
        client_id: str,
        *,
        redirect_uris: Iterable[str],
        allowed: Iterable[str] = (),
        owner_premium: bool = True,
        grant_scope: str | None = None,
        rotate_refresh: bool = True,
    ) -> App:
        if CLIENT_ID.fullmatch(client_id) is None:
            raise ValueError("a Client ID is 32 lower-case hex characters")
        app = App(
            client_id,
            set(redirect_uris),
            set(allowed),
            owner_premium=owner_premium,
            grant_scope=grant_scope,
            rotate_refresh=rotate_refresh,
        )
        self.apps[client_id] = app
        return app

    def sign_in(self, user: str | None) -> None:
        """Who is signed in at the authorize page (approves it)."""
        self.signed_in = user
        self.denying = False

    def deny(self) -> None:
        """The next authorizations are cancelled (``error=access_denied``)."""
        self.denying = True

    def add_playlist(
        self,
        owner: str,
        name: str,
        entries: Iterable[str | None] = (),
        *,
        public: bool = False,
        collaborative: bool = False,
        description: str = "",
    ) -> Playlist:
        with self.lock:
            return self._new_playlist(owner, name, description, public, collaborative, entries)

    def grant(self, client_id: str, user: str, scope: str = SCOPES) -> tuple[str, str]:
        """An access and refresh token as if ``user`` had connected ``client_id``."""
        with self.lock:
            access, refresh = self._issue(Grant(client_id, user, scope), with_refresh=True)
        assert refresh is not None
        return access, refresh

    def expire(self, access_token: str) -> None:
        with self.lock:
            self.access[access_token].expires_at = self.clock() - 1

    def inject(self, kind: str, **options: Any) -> Injection:
        if kind not in KINDS:
            raise ValueError(f"unknown injection {kind!r}")
        injection = Injection(kind, **options)
        with self.lock:
            self.injections.append(injection)
        return injection

    def requests(self, host: str | None = None, path: str | None = None) -> list[Seen]:
        with self.lock:
            return [
                s
                for s in self.seen
                if (host is None or s.host == host)
                and (path is None or re.fullmatch(path, s.path) is not None)
            ]

    # --- one request -----------------------------------------------------------------------

    def handle(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Answer:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        method = method.upper()
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        lowered = {k.lower(): v for k, v in headers.items()}
        authorization = lowered.get("authorization")
        with self.lock:
            self._count += 1
            seen = Seen(
                self._count, method, host, parts.path, query, authorization, query.get("client_id")
            )
            self.seen.append(seen)
            grant = self._bearer(host, authorization)
            if grant is not None:
                seen.token_app, seen.token_user = grant.client_id, grant.user
            injection = self._take_injection(seen.number, method, parts.path)
            if injection is not None and injection.kind == "refuse":
                seen.outcome = "refused"
                return Answer(refuse=True)
            if injection is not None and injection.kind in _ANSWER_ONLY:
                answer = self._injected_error(injection)
            elif host == ACCOUNTS_HOST:
                answer = self._accounts(seen, lowered, body or b"")
            elif host == API_HOST:
                answer = self._api(seen, grant, body or b"", injection)
            else:
                answer = _error(404, "Unknown host")
            if injection is not None:
                answer = self._after(answer, injection)
            seen.status = answer.status
            if answer.drop:
                seen.outcome = "dropped"
            return answer

    def _take_injection(self, number: int, method: str, path: str) -> Injection | None:
        for injection in self.injections:
            if injection.matches(number, method, path):
                injection.times -= 1
                return injection
        return None

    def _injected_error(self, injection: Injection) -> Answer:
        if injection.kind in ("invalid_grant", "invalid_client"):
            return _oauth_error(injection.kind, injection.message or "Injected failure")
        if injection.kind == "rate_limited":
            answer = _error(429, injection.message or "API rate limit exceeded")
            answer.headers["Retry-After"] = str(injection.retry_after or 1)
            return answer
        return _error(injection.status or 500, injection.message or "Injected failure")

    def _after(self, answer: Answer, injection: Injection) -> Answer:
        if injection.kind == "drop":
            answer.drop = True
        elif injection.kind == "trickle":
            answer.trickle = injection.delay
        elif injection.kind == "noid" and answer.status < 300 and answer.body:
            data = json.loads(answer.body)
            if isinstance(data, dict):
                data.pop("id", None)
                data.pop("snapshot_id", None)
                answer.body = json.dumps(data).encode()
        return answer

    # --- accounts.spotify.com --------------------------------------------------------------

    def _accounts(self, seen: Seen, headers: dict[str, str], body: bytes) -> Answer:
        if seen.method == "GET" and seen.path == "/authorize":
            return self._authorize(seen.query)
        if seen.method == "POST" and seen.path == "/api/token":
            form = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
            seen.client_id = form.get("client_id")
            if "client_secret" in form or (seen.authorization or "").lower().startswith("basic"):
                seen.client_secret = True
                return _oauth_error("invalid_request", "No client secret is accepted")
            if headers.get("content-type", "").split(";")[0].strip() != (
                "application/x-www-form-urlencoded"
            ):
                return _oauth_error("invalid_request", "Form body expected")
            return self._token(form)
        return _error(404, "Not found")

    def _authorize(self, query: dict[str, str]) -> Answer:
        app = self.apps.get(query.get("client_id", ""))
        if app is None:
            return Answer(400, b"INVALID_CLIENT: Invalid client", {"Content-Type": "text/plain"})
        redirect_uri = query.get("redirect_uri", "")
        if redirect_uri not in app.redirect_uris:
            return Answer(
                400, b"INVALID_CLIENT: Invalid redirect URI", {"Content-Type": "text/plain"}
            )
        state = {"state": query["state"]} if "state" in query else {}
        if query.get("response_type") != "code":
            return _redirect(redirect_uri, {"error": "unsupported_response_type", **state})
        challenge = query.get("code_challenge", "")
        if query.get("code_challenge_method") != "S256" or not challenge:
            return _redirect(redirect_uri, {"error": "invalid_request", **state})
        if self.denying:
            return _redirect(redirect_uri, {"error": "access_denied", **state})
        if self.signed_in is None:
            return Answer(400, b"no user is signed in at the fake", {"Content-Type": "text/plain"})
        code = "fake-code-" + secrets.token_urlsafe(24)
        self.codes[code] = Code(
            app.client_id,
            self.signed_in,
            redirect_uri,
            challenge,
            query.get("scope", ""),
            self.clock() + CODE_SECONDS,
        )
        return _redirect(redirect_uri, {"code": code, **state})

    def _token(self, form: dict[str, str]) -> Answer:
        app = self.apps.get(form.get("client_id", ""))
        if app is None:
            return _oauth_error("invalid_client", "Invalid client")
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            code = self.codes.pop(form.get("code", ""), None)  # single use, whatever follows
            if code is None or code.client_id != app.client_id or code.expires_at <= self.clock():
                return _oauth_error("invalid_grant", "Invalid authorization code")
            if form.get("redirect_uri") != code.redirect_uri:
                return _oauth_error("invalid_grant", "Invalid redirect URI")
            verifier = form.get("code_verifier", "")
            if VERIFIER.fullmatch(verifier) is None or challenge_of(verifier) != code.challenge:
                return _oauth_error("invalid_grant", "code_verifier was incorrect")
            scope = app.grant_scope if app.grant_scope is not None else code.scope
            access, refresh = self._issue(Grant(app.client_id, code.user, scope), with_refresh=True)
            return _json(200, _token_answer(access, refresh, scope))
        if grant_type == "refresh_token":
            given = form.get("refresh_token", "")
            held = self.refresh.get(given)
            if held is None or held.client_id != app.client_id:
                return _oauth_error("invalid_grant", "Invalid refresh token")
            access, refresh = self._issue(
                Grant(app.client_id, held.user, held.scope), with_refresh=app.rotate_refresh
            )
            if refresh is not None:
                del self.refresh[given]  # rotated: the old one is dead
            return _json(200, _token_answer(access, refresh, held.scope))
        return _oauth_error("unsupported_grant_type", "grant_type must be one of the two")

    def _issue(self, grant: Grant, *, with_refresh: bool) -> tuple[str, str | None]:
        access = "fake-at-" + secrets.token_urlsafe(24)
        self.access[access] = Grant(
            grant.client_id, grant.user, grant.scope, self.clock() + ACCESS_SECONDS
        )
        refresh = None
        if with_refresh:
            refresh = "fake-rt-" + secrets.token_urlsafe(24)
            self.refresh[refresh] = Grant(grant.client_id, grant.user, grant.scope)
        return access, refresh

    # --- api.spotify.com -------------------------------------------------------------------

    def _bearer(self, host: str, authorization: str | None) -> Grant | None:
        if host != API_HOST or authorization is None or not authorization.startswith("Bearer "):
            return None
        return self.access.get(authorization.removeprefix("Bearer "))

    def _api(
        self, seen: Seen, grant: Grant | None, body: bytes, injection: Injection | None
    ) -> Answer:
        if grant is None:
            return _error(401, "Invalid access token")
        if grant.expires_at <= self.clock():
            return _error(401, "The access token expired")
        app = self.apps.get(grant.client_id)
        if app is None or grant.user not in app.allowed or not app.owner_premium:
            return _error(403, NOT_ALLOWLISTED)
        user, path, method = grant.user, seen.path, seen.method
        page_kind = injection.kind if injection is not None else None
        if path == "/v1/me" and method == "GET":
            return _json(200, self._user(user))
        if path == "/v1/search" and method == "GET":
            return self._search(seen.query)
        if path == "/v1/me/playlists":
            if method == "POST":
                return self._create(user, body)
            if method == "GET":
                return self._my_playlists(user, seen.query, page_kind)
        match = re.fullmatch(r"/v1/playlists/([^/]+)(/items)?", path)
        if match is None:
            return _error(404, "Service not found")
        playlist_id, items = match.group(1), match.group(2) is not None
        if ID.fullmatch(playlist_id) is None:
            return _error(400, "Invalid base62 id")
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return _error(404, "Resource not found")
        mine = user == playlist.owner or user in playlist.collaborators
        if not mine and self.foreign == "404":
            return _error(404, "Resource not found")
        if not items:
            return (
                _json(200, self._playlist(playlist, with_total=mine))
                if method == "GET"
                else (_error(405, "Method not allowed"))
            )
        if not mine:
            return _error(403, "You cannot access this playlist's items")
        if method == "GET":
            return self._items(
                playlist, seen.query, injection if page_kind in _PAGE_KINDS else None
            )
        if method in ("PUT", "POST"):
            partial = injection if page_kind == "partial" else None
            return self._write(playlist, method, body, partial)
        return _error(405, "Method not allowed")

    def _user(self, user: str) -> dict[str, Any]:
        return {
            "id": user,
            "display_name": self.users.get(user, user),
            "type": "user",
            "uri": f"spotify:user:{user}",
            "href": f"{API}/users/{user}",
            "external_urls": {"spotify": f"https://open.spotify.com/user/{user}"},
        }

    def _track(self, tid: str) -> dict[str, Any]:
        t = self.catalogue[tid]
        return {
            "id": tid,
            "type": "track",
            "name": t["name"],
            "uri": f"spotify:track:{tid}",
            "href": f"{API}/tracks/{tid}",
            "external_urls": {"spotify": f"https://open.spotify.com/track/{tid}"},
            "preview_url": f"https://p.scdn.co/mp3-preview/{tid}",
            "is_local": False,
            "duration_ms": t["duration_ms"],
            "artists": [{"id": t["artist_id"], "name": t["artist"], "type": "artist"}],
            "album": {
                "id": t["album_id"],
                "name": t["album"],
                "release_date": t["release_date"],
                "images": [{"url": f"https://i.scdn.co/image/{t['album_id']}"}],
            },
        }

    def _search(self, query: dict[str, str]) -> Answer:
        if "track" not in query.get("type", "").split(","):
            return _error(400, "Missing or bad type")
        words = query.get("q", "").lower().split()
        if not words:
            return _error(400, "No search query")
        limit, offset, problem = _paging(query, SEARCH_LIMIT, SEARCH_LIMIT)
        if problem is not None:
            return problem
        hits = [
            tid
            for tid, t in self.catalogue.items()
            if all(w in f"{t['name']} {t['artist']}".lower() for w in words)
        ]
        page = [self._track(tid) for tid in hits[offset : offset + limit]]
        base = f"{API}/search?" + urlencode({"q": query["q"], "type": "track"})
        return _json(200, {"tracks": _page(base, page, len(hits), limit, offset)})

    def _new_playlist(
        self,
        owner: str,
        name: str,
        description: str,
        public: bool,
        collaborative: bool,
        entries: Iterable[str | None],
    ) -> Playlist:
        self._next_playlist += 1
        playlist = Playlist(
            f"FakeList{self._next_playlist:014d}",
            owner,
            name,
            description,
            public,
            collaborative,
            list(entries),
        )
        self.playlists[playlist.id] = playlist
        return playlist

    def _create(self, user: str, body: bytes) -> Answer:
        try:
            data = json.loads(body)
        except ValueError:
            return _error(400, "Error parsing JSON")
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name:
            return _error(400, "Missing required field: name")
        public = data.get("public", True)  # Spotify's default when it is left out
        collaborative = data.get("collaborative", False)
        if not isinstance(public, bool) or not isinstance(collaborative, bool):
            return _error(400, "Invalid boolean")
        if collaborative and public:
            return _error(400, "Collaborative playlists cannot be public")
        description = data.get("description", "")
        playlist = self._new_playlist(
            user,
            name,
            description if isinstance(description, str) else "",
            public,
            collaborative,
            (),
        )
        return _json(201, self._playlist(playlist, with_total=True))

    def _playlist(self, playlist: Playlist, *, with_total: bool) -> dict[str, Any]:
        items: dict[str, Any] = {"href": f"{API}/playlists/{playlist.id}/items"}
        if with_total:
            items["total"] = len(playlist.entries)
        return {
            "id": playlist.id,
            "type": "playlist",
            "name": playlist.name,
            "description": playlist.description,
            "public": playlist.public,
            "collaborative": playlist.collaborative,
            "snapshot_id": playlist.snapshot_id,
            "owner": {
                "id": playlist.owner,
                "type": "user",
                "uri": f"spotify:user:{playlist.owner}",
            },
            "items": items,
            "href": f"{API}/playlists/{playlist.id}",
            "uri": f"spotify:playlist:{playlist.id}",
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist.id}"},
            "images": [],
        }

    def _entry(self, playlist: Playlist, uri: str | None) -> dict[str, Any]:
        obj: dict[str, Any] | None
        local = False
        if uri is None:
            obj = None
        elif (m := TRACK_URI.fullmatch(uri)) is not None and m.group(1) in self.catalogue:
            obj = self._track(m.group(1))
        elif (m := EPISODE_URI.fullmatch(uri)) is not None:
            obj = {"id": m.group(1), "type": "episode", "uri": uri, "name": "Invented episode"}
        else:  # a local file (or an unknown URI): no id
            local = True
            obj = {"id": None, "type": "track", "uri": uri, "is_local": True, "name": "Local file"}
        return {
            "added_at": "2026-10-30T00:00:00Z",
            "added_by": {"id": playlist.owner},
            "is_local": local,
            self.item_shape: obj,
        }

    def _items(
        self, playlist: Playlist, query: dict[str, str], injection: Injection | None
    ) -> Answer:
        limit, offset, problem = _paging(query, PAGE_LIMIT, PAGE_LIMIT)
        if problem is not None:
            return problem
        entries = [self._entry(playlist, u) for u in playlist.entries[offset : offset + limit]]
        base = f"{API}/playlists/{playlist.id}/items"
        page = _page(base, entries, len(playlist.entries), limit, offset)
        if injection is not None:
            _spoil(page, injection.kind, self.item_shape)
        return _json(200, page)

    def _write(
        self, playlist: Playlist, method: str, body: bytes, partial: Injection | None
    ) -> Answer:
        try:
            data = json.loads(body)
        except ValueError:
            return _error(400, "Error parsing JSON")
        uris = data.get("uris") if isinstance(data, dict) else None
        if not isinstance(uris, list):
            return _error(400, "Missing uris")
        if len(uris) > WRITE_LIMIT:
            return _error(400, "Too many ids requested")
        for uri in uris:
            if not isinstance(uri, str) or (
                TRACK_URI.fullmatch(uri) is None and EPISODE_URI.fullmatch(uri) is None
            ):
                return _error(400, "Invalid track uri")
        status = 200 if method == "PUT" else 201
        if partial is not None:
            uris = uris[: partial.keep if partial.keep is not None else len(uris) // 2]
            status = partial.status or status
        if method == "PUT":
            playlist.entries = list(uris)
        else:
            playlist.entries.extend(uris)
        playlist.version += 1
        if status >= 400:
            return _error(status, "Injected failure after a partial write")
        return _json(status, {"snapshot_id": playlist.snapshot_id})

    def _my_playlists(self, user: str, query: dict[str, str], page_kind: str | None) -> Answer:
        limit, offset, problem = _paging(query, 20, PAGE_LIMIT)
        if problem is not None:
            return problem
        mine = [
            p
            for p in reversed(self.playlists.values())
            if p.owner == user or user in p.collaborators
        ]
        listed = [self._playlist(p, with_total=True) for p in mine[offset : offset + limit]]
        page = _page(f"{API}/me/playlists", listed, len(mine), limit, offset)
        if page_kind == "next_unlisted":
            _spoil(page, page_kind, self.item_shape)
        return _json(200, page)

    # --- what tests and the e2e control read -----------------------------------------------

    def summary(self) -> dict[str, Any]:
        """State without any token, code or verifier (the e2e ``state`` control)."""
        with self.lock:
            return {
                "requests": [
                    {
                        "number": s.number,
                        "method": s.method,
                        "host": s.host,
                        "path": s.path,
                        "client_id": s.client_id,
                        "token_app": s.token_app,
                        "token_user": s.token_user,
                        "authorization": s.authorization is not None,
                        "client_secret": s.client_secret,
                        "outcome": s.outcome,
                        "status": s.status,
                    }
                    for s in self.seen
                ],
                "playlists": [
                    {
                        "id": p.id,
                        "owner": p.owner,
                        "name": p.name,
                        "public": p.public,
                        "collaborative": p.collaborative,
                        "entries": p.entries,
                    }
                    for p in self.playlists.values()
                ],
                "injections": [
                    {"kind": i.kind, "at": i.at, "path": i.path, "times": i.times}
                    for i in self.injections
                    if i.times > 0
                ],
            }


def _paging(query: dict[str, str], default: int, maximum: int) -> tuple[int, int, Answer | None]:
    try:
        limit = int(query.get("limit", default))
        offset = int(query.get("offset", 0))
    except ValueError:
        return 0, 0, _error(400, "Invalid limit or offset")
    if not 1 <= limit <= maximum:
        return 0, 0, _error(400, "Invalid limit")
    if offset < 0:
        return 0, 0, _error(400, "Invalid offset")
    return limit, offset, None


def _page(base: str, items: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    sep = "&" if "?" in base else "?"

    def link(at: int) -> str:
        return f"{base}{sep}offset={at}&limit={limit}"

    return {
        "href": link(offset),
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "next": link(offset + limit) if offset + limit < total else None,
        "previous": link(max(0, offset - limit)) if offset > 0 else None,
    }


def _spoil(page: dict[str, Any], kind: str, shape: str) -> None:
    """A page as one of the ``_PAGE_KINDS`` injections answers it (the playlist is unchanged)."""
    items: list[Any] = page["items"]
    if kind == "next_unlisted":
        page["next"] = f"https://{UNLISTED_HOST}/v1/next?offset={page['offset'] + page['limit']}"
        return
    if kind == "duplicate":
        if items:
            items.insert(0, json.loads(json.dumps(items[0])))
        return
    obj: dict[str, Any] | None = None  # "null": the entry's item is null
    if kind == "local":
        uri = "spotify:local:Nobody:Demo+Tape:Tape:180"
        obj = {"id": None, "type": "track", "uri": uri, "is_local": True, "name": "Tape"}
    elif kind == "episode":
        eid = "FakeEpisode00000000001"
        obj = {"id": eid, "type": "episode", "uri": f"spotify:episode:{eid}", "name": "Episode"}
    entry = {"added_at": "2026-10-30T00:00:00Z", "is_local": kind == "local", shape: obj}
    items.insert(0, entry)


def _json(status: int, data: Any) -> Answer:
    return Answer(status, json.dumps(data).encode(), {"Content-Type": "application/json"})


def _error(status: int, message: str) -> Answer:
    return _json(status, {"error": {"status": status, "message": message}})


def _oauth_error(code: str, description: str) -> Answer:
    return _json(400, {"error": code, "error_description": description})


def _redirect(uri: str, params: dict[str, str]) -> Answer:
    sep = "&" if "?" in uri else "?"
    return Answer(302, b"", {"Location": f"{uri}{sep}{urlencode(params)}"})


def _token_answer(access: str, refresh: str | None, scope: str) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "access_token": access,
        "token_type": "Bearer",
        "scope": scope,
        "expires_in": ACCESS_SECONDS,
    }
    if refresh is not None:
        answer["refresh_token"] = refresh
    return answer
