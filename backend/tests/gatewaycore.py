"""A fake of the SwedeBeat gateway (ADR 0035 section 9): ``/auth/login``, ``/auth/session``,
``/catalog/match``, ``GET /tracks/{reference}/download`` and, since SR C7, ``POST /playlists``,
``POST /playlists/{id}/publish``, ``POST /playlists/merge-publish`` and ``GET /playlists/shared``
(ADR 0036 section 8) of the contract (``docs/api.md`` of ``magpern/sb-gateway-service``), for the
backend tests and the e2e stack.

TEST ONLY, public test values only, no network of its own. The same module serves both:
``gatewayfake.py`` runs it in process over an httpx transport, and ``e2e/fake-gateway/server.py``
(mounted read-only) carries it over TLS as ``gateway.classbeat-e2e.test``. It knows no SwedeBeat
secret and talks to nothing.

It keeps the contract's order of checks: the body size (``413`` over 2 MB), then ``X-API-Key``
(``401 {"detail": "unknown client"}``), then the bearer on every route but the login (``401
"missing, invalid or expired bearer; POST /auth/login"``), then the schema (``422`` with a
**list** ``detail``), then the work. A refused login is ``401`` with a relayed message. Errors
are ``{"detail": <string>}`` otherwise.

- **Login** issues an opaque bearer per login (two users get two bearers) whose expiry the test
  can move (``advance``, ``expire``); unknown fields (``force``) are ignored, as pydantic does.
  ``issue`` makes one without a login, for a connection a test stores itself.
- **Match** answers ``tracks`` mode only, one item per track in order, from ``catalogue``
  (``(title, artist)`` folded, to ``found`` or ``unsure``; anything else is ``missing``).
  ``playlist`` mode is a ``422`` here: classbeat never sends it (ADR 0035 section 7). Each
  catalogue track's ``reference`` is ``reference_of(title, artist)``, the same on every answer.
  ``add_flaky`` makes a catalogue track answer ``missing`` a few times first (a transient miss).
- **Download** serves ``audio`` (reference to bytes): every catalogue track has ``FIXTURE_MP3``
  (a few silent MPEG-1 Layer III frames, generated here), and ``add_audio`` adds a test's own.
  After the key and the bearer: a reference outside the gateway's shape is ``422`` (a string
  ``detail``), one without a file ``404``. The answer is ``audio/mpeg`` with
  ``Content-Disposition``, as the gateway streams it.
- **Playlists** (SR C7): a create checks the body as the gateway does (name 1-120, 1-60 tracks,
  each reference with a file, a crossfade 0-30 and shorter than the played part, ``trim: false``
  with a cut refused) and keeps the playlist **unpublished** under a new id; a publish needs the
  JSON literal ``{"confirm_irreversible": true}`` and the caller's own id; ``/playlists/shared``
  lists the caller's published playlists, ``shared_page_size`` per page (``page`` 1-50); a
  merge-publish needs ``confirm_irreversible=true`` (``400``), a ``name``, an ``X-Tracks`` list of
  1-200 items (``422``) and an MP3 body (``422`` otherwise, ``413`` over 400 MB) and answers an S3
  URL of the published mix (``mixes``). With ``writes_enabled`` false, create, publish and merge
  answer ``403 {"detail": "SwedeBeat writes are disabled"}`` after the checks, as the gateway.
- **The record** (``requests``) holds every request's method, path, headers (the key and the
  bearer too, so a test can check exactly what was sent), body and the account its bearer
  belongs to. ``public_state`` is the same without any header value, password or bearer, for
  the e2e control.
- **Injections** (``inject``) replace the next answer of a path, in the contract's place:
  ``unknown_client`` at the key check, ``bearer_401`` at the bearer check, and after them
  ``status`` (any status, with ``detail``), ``rate_limited`` (``429`` with ``Retry-After``),
  ``slow`` (the answer starts after ``seconds``), ``trickle`` (its body one byte per
  ``seconds``), ``oversize`` (a valid answer padded to ``size`` bytes), ``gzip`` (the answer
  gzipped), ``redirect`` (``3xx`` to ``location``), ``wrong_count`` (one item too few),
  ``all_missing``, ``body`` (a raw ``200`` answer of the test's own) and ``raw`` (a raw answer
  with ``status``); for any answer ``content_type`` (``value``), ``declared`` (a
  ``Content-Length`` of ``size``, whatever the body) and ``no_length`` (none at all). ``oversize``
  pads an audio answer with ``size`` bytes. For the writes: ``status`` replaces the answer
  **after** the work (a 5xx after the write took effect), ``refuse`` answers ``status`` **before**
  it (nothing written), ``drop`` does the work and cuts the connection before the answer (a
  timeout after sending) and ``connect_error`` refuses the connection before anything is sent.
"""

import gzip
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qsl

LOGIN_PATH = "/auth/login"
SESSION_PATH = "/auth/session"
MATCH_PATH = "/catalog/match"
HEALTH_PATH = "/health"
DOWNLOAD_ROUTE = re.compile(r"/tracks/([^/]*)/download")
CREATE_PATH = "/playlists"
PUBLISH_ROUTE = re.compile(r"/playlists/([^/]*)/publish")
MERGE_PATH = "/playlists/merge-publish"
SHARED_PATH = "/playlists/shared"
PLAYLIST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
MIX_MAX = 400 * 1024 * 1024
WRITES_OFF = "SwedeBeat writes are disabled"
MIX_BUCKET = "https://fake-mixes.s3.eu-north-1.amazonaws.com"
SAFE_REFERENCE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # the gateway's is_safe_reference
NO_FILE = "no file for that reference"
# Silent MPEG-1 Layer III frames (128 kbit/s, 44.1 kHz, 417 bytes each: the header and zeros).
FIXTURE_MP3 = (bytes.fromhex("fffb9064") + bytes(413)) * 24
CONTROL = "/__e2e/"
BODY_MAX = 2 * 1024 * 1024
UNKNOWN_CLIENT = "unknown client"
BEARER_REFUSED = "missing, invalid or expired bearer; POST /auth/login"
WRONG_LOGIN = "Wrong email or password"
TTL_SECONDS = 86400
FOUND_SCORE = 0.93
UNSURE_SCORE = 0.61
UNSURE_TITLE = "A Different Tune Entirely"
SECONDS = 240  # every catalogue track's length
_JSON = {"content-type": "application/json"}


@dataclass
class Reply:
    """One answer: ``delay`` seconds before it starts, ``trickle`` seconds between its bytes."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=lambda: dict(_JSON))
    delay: float = 0.0
    trickle: float = 0.0
    declared: int | None = None  # the Content-Length sent; None: the body's own length
    no_length: bool = False  # send no Content-Length (the body ends with the connection)
    drop: bool = False  # cut the connection instead of answering (the work is done)


@dataclass
class Session:
    email: str
    expires_at: float
    customer_id: str


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]  # lower-case names
    body: Any  # the parsed JSON, or None
    email: str | None  # the account of the bearer it carried, if any
    query: str = ""  # the raw query string, if any
    size: int = 0  # the body's length in bytes


@dataclass
class Injection:
    kind: str
    path: str | None  # None: the next request of any path
    status: int = 502
    seconds: float = 0.0
    retry_after: str = "30"
    location: str = "/elsewhere"
    size: int = 0
    body: bytes = b""
    value: str = ""


def _reply(status: int, payload: object, headers: dict[str, str] | None = None) -> Reply:
    return Reply(status, json.dumps(payload).encode(), {**_JSON, **(headers or {})})


def _schema_error(loc: str) -> Reply:
    # The schema 422: a list detail that never echoes the submitted values.
    return _reply(422, {"detail": [{"type": "value_error", "loc": ["body", loc], "msg": "bad"}]})


def reference_of(title: str, artist: str) -> str:
    """The fake catalogue's stable reference of a track (the folded title and artist)."""
    folded = f"{title.strip().casefold()}\n{artist.strip().casefold()}".encode()
    return "ref-" + hashlib.sha256(folded).hexdigest()[:16]


def _utf8(value: str) -> str:
    """A header value as the gateway reads it (UTF-8), whether the server decoded it as latin-1
    (``http.server``) or httpx already as UTF-8."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def _text(value: object, low: int, high: int) -> bool:
    return isinstance(value, str) and low <= len(value) <= high


@dataclass
class FakeGateway:
    client_keys: set[str]
    users: dict[str, str] = field(default_factory=lambda: dict[str, str]())  # email -> password
    bearers: dict[str, Session] = field(default_factory=lambda: dict[str, Session]())
    catalogue: dict[tuple[str, str], str] = field(
        default_factory=lambda: dict[tuple[str, str], str]()
    )
    requests: list[Recorded] = field(default_factory=lambda: list[Recorded]())
    injections: list[Injection] = field(default_factory=lambda: list[Injection]())
    ttl: int = TTL_SECONDS
    offset: float = 0.0  # added to the clock: a test moves time with ``advance``
    # Fields added to every answered track (planted values a test checks are never kept).
    track_extras: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    audio: dict[str, bytes] = field(default_factory=lambda: dict[str, bytes]())
    # A catalogue track answered ``missing`` this many more times first (a transient miss).
    flaky: dict[tuple[str, str], int] = field(default_factory=lambda: dict[tuple[str, str], int]())
    # SR C7: the playlists by id ({email, name, tracks, public}) and the merged mixes, in order.
    playlists: dict[str, dict[str, Any]] = field(
        default_factory=lambda: dict[str, dict[str, Any]]()
    )
    mixes: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    writes_enabled: bool = True
    shared_page_size: int = 2

    def add_user(self, email: str, password: str) -> None:
        self.users[email] = password

    def now(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def expire(self, bearer: str) -> None:
        self.bearers[bearer].expires_at = self.now() - 1

    def add_track(self, title: str, artist: str, status: str) -> None:
        """``status`` is ``found`` or ``unsure``; any other song is ``missing``."""
        self.catalogue[(title.strip().casefold(), artist.strip().casefold())] = status
        self.audio[reference_of(title, artist)] = FIXTURE_MP3

    def add_flaky(self, title: str, artist: str, misses: int = 1) -> None:
        """The catalogue track ``(title, artist)`` answers ``missing`` ``misses`` more times."""
        self.flaky[(title.strip().casefold(), artist.strip().casefold())] = misses

    def add_audio(self, reference: str, body: bytes) -> None:
        self.audio[reference] = body

    def inject(self, kind: str, path: str | None = None, **params: Any) -> None:
        self.injections.append(Injection(kind, path, **params))

    def issue(self, email: str) -> str:
        """A bearer for ``email`` as a login would issue it (a test's stored connection)."""
        bearer = "gAAAAB" + secrets.token_urlsafe(48)
        self.bearers[bearer] = Session(email, self.now() + self.ttl, str(35900 + len(self.bearers)))
        return bearer

    def reset(self) -> None:
        self.bearers.clear()
        self.catalogue.clear()
        self.audio.clear()
        self.flaky.clear()
        self.playlists.clear()
        self.mixes.clear()
        self.writes_enabled = True
        self.requests.clear()
        self.injections.clear()
        self.offset = 0.0

    def _take(self, path: str, kinds: tuple[str, ...]) -> Injection | None:
        for injection in self.injections:
            if injection.kind in kinds and injection.path in (None, path):
                self.injections.remove(injection)
                return injection
        return None

    def handle(self, method: str, path: str, headers: dict[str, str], raw: bytes) -> Reply:
        """The answer to one request (headers with lower-case names)."""
        route, _, query = path.partition("?")
        if route == HEALTH_PATH:
            return _reply(200, {"status": "ok"})
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = None
        bearer = headers.get("authorization", "").removeprefix("Bearer ")
        session = self.bearers.get(bearer)
        self.requests.append(
            Recorded(
                method, route, dict(headers), body, session.email if session else None, query,
                len(raw),
            )
        )  # fmt: skip
        if len(raw) > (MIX_MAX if route == MERGE_PATH else BODY_MAX):
            return _reply(413, {"detail": "request body too large"})
        if headers.get("x-api-key") not in self.client_keys or self._take(
            route, ("unknown_client",)
        ):
            return _reply(401, {"detail": UNKNOWN_CLIENT})
        if route != LOGIN_PATH:
            valid = session is not None and session.expires_at > self.now()
            if not valid or self._take(route, ("bearer_401",)):
                return _reply(401, {"detail": BEARER_REFUSED})
        if (route, method) == (LOGIN_PATH, "POST"):
            answer = self._login(body)
        elif (route, method) == (SESSION_PATH, "GET"):
            assert session is not None
            answer = _reply(200, {"valid": True, "customer_id": session.customer_id})
        elif (route, method) == (MATCH_PATH, "POST"):
            answer = self._match(body, route)
        elif method == "GET" and (found := DOWNLOAD_ROUTE.fullmatch(route)):
            answer = self._download(found.group(1))
        elif route in (CREATE_PATH, MERGE_PATH, SHARED_PATH) or PUBLISH_ROUTE.fullmatch(route):
            assert session is not None
            refused = self._take(route, ("refuse",))
            if refused is not None:
                return _reply(refused.status, {"detail": "injected before the work"})
            answer = self._playlists(method, route, query, headers, body, raw, session)
            if answer is None:
                return _reply(405, {"detail": "Method Not Allowed"})
            if self._take(route, ("drop",)):
                answer.drop = True
        else:
            return _reply(404, {"detail": "Not Found"})
        return self._injected(route, answer)

    def _playlists(
        self,
        method: str,
        route: str,
        query: str,
        headers: dict[str, str],
        body: Any,
        raw: bytes,
        session: Session,
    ) -> Reply | None:
        if (route, method) == (SHARED_PATH, "GET"):
            return self._shared(query, session)
        if method != "POST":
            return None
        if route == CREATE_PATH:
            return self._create(body, session)
        if route == MERGE_PATH:
            return self._merge(query, headers, raw, session)
        found = PUBLISH_ROUTE.fullmatch(route)
        assert found is not None
        return self._publish(found.group(1), body, session)

    def _write_guard(self) -> Reply | None:
        return None if self.writes_enabled else _reply(403, {"detail": WRITES_OFF})

    def _create(self, body: Any, session: Session) -> Reply:
        if not isinstance(body, dict):
            return _schema_error("body")
        fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        name, tracks = fields.get("name"), fields.get("tracks")
        if not _text(name, 1, 120):
            return _schema_error("name")
        if not isinstance(tracks, list) or not 1 <= len(tracks) <= 60:  # pyright: ignore[reportUnknownArgumentType]
            return _schema_error("tracks")
        crossfade = fields.get("crossfade_sec", 5)
        if not isinstance(crossfade, int) or not 0 <= crossfade <= 30:
            return _schema_error("crossfade_sec")
        if (guarded := self._write_guard()) is not None:
            return guarded
        checked: list[dict[str, Any]] = []
        for n, track in enumerate(tracks):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            if not isinstance(track, dict) or not SAFE_REFERENCE.fullmatch(
                str(track.get("reference"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            ):
                return _schema_error("tracks")
            item: dict[str, Any] = track  # pyright: ignore[reportUnknownVariableType]
            if item["reference"] not in self.audio:
                return _reply(404, {"detail": NO_FILE})
            start = float(item.get("start_sec") or 0.0)
            end = min(float(item.get("end_sec") or SECONDS), float(SECONDS))
            cut = start > 0 or end < SECONDS
            if end <= start or (item.get("trim") is False and cut):
                return _reply(422, {"detail": "a cut that does not fit the track"})
            last = n == len(tracks) - 1  # pyright: ignore[reportUnknownArgumentType]
            fade = 0 if last else item.get("crossfade_sec", crossfade)
            if not isinstance(fade, int) or fade < 0 or fade >= end - start:
                return _reply(422, {"detail": "crossfade longer than the played part"})
            checked.append({**item, "crossfade_sec": fade})
        playlist_id = f"pl-{len(self.playlists) + 1:04d}-{secrets.token_hex(4)}"
        self.playlists[playlist_id] = {
            "email": session.email, "name": name, "tracks": checked, "public": False,
        }  # fmt: skip
        echo = {"playlist_id": playlist_id, "file_url": f"{MIX_BUCKET}/{playlist_id}.zip"}
        return _reply(201, {**echo, "track_count": len(checked), "tracks": checked})

    def _publish(self, playlist_id: str, body: Any, session: Session) -> Reply:
        if not PLAYLIST_ID.fullmatch(playlist_id):
            return _reply(422, {"detail": "invalid playlist id"})
        if not isinstance(body, dict) or body.get("confirm_irreversible") is not True:  # pyright: ignore[reportUnknownMemberType]
            return _schema_error("confirm_irreversible")
        if (guarded := self._write_guard()) is not None:
            return guarded
        found = self.playlists.get(playlist_id)
        if found is None or found["email"] != session.email:
            return _reply(502, {"detail": "SwedeBeat failed"})
        found["public"] = True
        return _reply(200, {"playlist_id": playlist_id, "public": True})

    def _shared(self, query: str, session: Session) -> Reply:
        page = dict(parse_qsl(query)).get("page", "1")
        if not page.isdigit() or not 1 <= int(page) <= 50:
            return _schema_error("page")
        own = [
            {"playlist_id": pid, "name": p["name"], "track_count": len(p["tracks"]),
             "shared_at": "2026-11-16T10:00:00", "download_count": 0}
            for pid, p in self.playlists.items()
            if p["public"] and p["email"] == session.email
        ]  # fmt: skip
        size = self.shared_page_size
        start = (int(page) - 1) * size
        return _reply(200, own[start : start + size])

    def _merge(self, query: str, headers: dict[str, str], raw: bytes, session: Session) -> Reply:
        params = dict(parse_qsl(query))
        name = params.get("name", "")
        if not _text(name, 1, 120):
            return _schema_error("name")
        if params.get("confirm_irreversible") != "true":
            return _reply(400, {"detail": "confirm_irreversible must be true"})
        if (guarded := self._write_guard()) is not None:
            return guarded
        try:
            tracks = json.loads(_utf8(headers.get("x-tracks", "")))
        except ValueError:
            tracks = None
        if not isinstance(tracks, list) or not 1 <= len(tracks) <= 200:  # pyright: ignore[reportUnknownArgumentType]
            return _reply(422, {"detail": "X-Tracks must be a JSON list"})
        if len(raw) > MIX_MAX:
            return _reply(413, {"detail": "mix too large"})
        if not raw.startswith((b"\xff\xfb", b"\xff\xf3", b"ID3")):
            return _reply(422, {"detail": "body is not a readable mp3"})
        safe = re.sub(r"[^a-zA-Z0-9.]", "", session.email)
        key = f"{safe}/{re.sub(r'[^a-zA-Z0-9.]', '_', name)}-{len(self.mixes) + 1}.mp3"
        url = f"{MIX_BUCKET}/{key}"
        digest = hashlib.sha256(raw).hexdigest()
        self.mixes.append(
            {"email": session.email, "name": name, "url": url, "tracks": tracks, "sha256": digest}
        )
        return _reply(200, {"url": url})

    def _login(self, body: Any) -> Reply:
        if not isinstance(body, dict):
            return _schema_error("body")
        fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        email, password = fields.get("email"), fields.get("password")
        device_id = fields.get("device_id")
        if not isinstance(email, str) or not _text(email, 3, 254):
            return _schema_error("email")
        if not isinstance(password, str) or not _text(password, 1, 256):
            return _schema_error("password")
        if device_id is not None and not _text(device_id, 0, 64):
            return _schema_error("device_id")
        if self.users.get(email) != password:
            return _reply(401, {"detail": WRONG_LOGIN})
        bearer = "gAAAAB" + secrets.token_urlsafe(48)
        customer = str(35900 + len(self.bearers))
        self.bearers[bearer] = Session(email, self.now() + self.ttl, customer)
        payload = {"bearer": bearer, "token_type": "Bearer", "expires_in": self.ttl}
        return _reply(200, {**payload, "customer_id": customer})

    def _download(self, reference: str) -> Reply:
        if not SAFE_REFERENCE.fullmatch(reference):
            return _reply(422, {"detail": "invalid reference"})
        audio = self.audio.get(reference)
        if audio is None:
            return _reply(404, {"detail": NO_FILE})
        disposition = f'attachment; filename="{reference}.mp3"'
        headers = {"content-type": "audio/mpeg", "content-disposition": disposition}
        return Reply(200, audio, headers)

    def _item(self, track: dict[str, str]) -> dict[str, Any]:
        title, artist = track["title"], track["artist"]
        key = (title.strip().casefold(), artist.strip().casefold())
        status = self.catalogue.get(key)
        if self.flaky.get(key, 0) > 0:
            self.flaky[key] -= 1
            status = None
        if status is None:
            return {
                "title": title,
                "artist": artist,
                "status": "missing",
                "score": 0.0,
                "track": None,
            }
        hit = title if status == "found" else UNSURE_TITLE
        score = FOUND_SCORE if status == "found" else UNSURE_SCORE
        found = {"reference": reference_of(title, artist), "title": hit, "artist": artist}
        found |= {"bpm": 128.0, "duration": float(SECONDS), **self.track_extras}
        return {"title": title, "artist": artist, "status": status, "score": score, "track": found}

    def _match(self, body: Any, route: str) -> Reply:
        if not isinstance(body, dict):
            return _schema_error("body")
        fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        if "playlist" in fields:
            return _reply(422, {"detail": "this fake answers the tracks mode only"})
        tracks = fields.get("tracks")
        if not isinstance(tracks, list) or len(tracks) > 200:  # pyright: ignore[reportUnknownArgumentType]
            return _schema_error("tracks")
        for track in tracks:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(track, dict) or not all(
                _text(track.get(k), 1, 300)  # pyright: ignore[reportUnknownMemberType]
                for k in ("title", "artist")
            ):
                return _schema_error("tracks")
        items = [self._item(t) for t in tracks]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        if self._take(route, ("all_missing",)):
            items = [{**i, "status": "missing", "score": 0.0, "track": None} for i in items]
        if self._take(route, ("wrong_count",)):
            items = items[:-1] if items else [self._item({"title": "x", "artist": "y"})]
        counts = {s: sum(i["status"] == s for i in items) for s in ("found", "unsure", "missing")}
        selected = [i["track"] for i in items if i["status"] == "found"]
        answer = {"playlist_name": None, **counts, "items": items, "selected": selected}
        return _reply(200, answer)

    def _injected(self, route: str, answer: Reply) -> Reply:
        kinds = ("status", "rate_limited", "slow", "trickle", "oversize", "gzip", "redirect")
        kinds += ("body", "raw", "content_type", "declared", "no_length")
        injection = self._take(route, kinds)
        if injection is None:
            return answer
        kind = injection.kind
        if kind == "status":
            return Reply(
                injection.status, json.dumps({"detail": "injected"}).encode(), drop=answer.drop
            )
        if kind == "rate_limited":
            return _reply(429, {"detail": "slow down"}, {"retry-after": injection.retry_after})
        if kind == "redirect":
            return _reply(injection.status, {}, {"location": injection.location})
        if kind in ("body", "raw"):
            return Reply(200 if kind == "body" else injection.status, injection.body)
        if kind == "slow":
            answer.delay = injection.seconds
        elif kind == "trickle":
            answer.trickle = injection.seconds
        elif kind == "content_type":
            answer.headers["content-type"] = injection.value
        elif kind == "declared":
            answer.declared = injection.size
        elif kind == "no_length":
            answer.no_length = True
        elif kind == "oversize" and not answer.headers["content-type"].startswith("application/"):
            answer.body += bytes(injection.size)
        elif kind == "oversize":
            padded = json.loads(answer.body) | {"pad": "x" * injection.size}
            answer.body = json.dumps(padded).encode()
        elif kind == "gzip":
            answer.body = gzip.compress(answer.body)
            answer.headers["content-encoding"] = "gzip"
        return answer

    def public_state(self) -> dict[str, Any]:
        """The record without a header value, a password, a key or a bearer (e2e control)."""
        calls: list[dict[str, Any]] = []
        for r in self.requests:
            body: Any = r.body
            if r.path == LOGIN_PATH and isinstance(body, dict):
                kept: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
                body = {k: v for k, v in kept.items() if k != "password"}
            calls.append(
                {"method": r.method, "path": r.path, "headers": sorted(r.headers), "body": body}
                | {"email": r.email}
            )
        mixes = [{k: m[k] for k in ("name", "url", "tracks")} for m in self.mixes]
        playlists = [
            {"name": p["name"], "public": p["public"], "tracks": len(p["tracks"])}
            for p in self.playlists.values()
        ]
        return {"requests": calls, "playlists": playlists, "mixes": mixes}

    def control(self, path: str, query: dict[str, str]) -> Reply:
        """The e2e stack's control (``/__e2e/...``): reset, state, catalogue, inject."""
        name = path.removeprefix(CONTROL)
        if name == "reset":
            self.reset()
        elif name == "state":
            return _reply(200, self.public_state())
        elif name == "catalogue":
            self.catalogue.clear()
            for status in ("found", "unsure"):
                for pair in filter(None, query.get(status, "").split("|")):
                    title, _, artist = pair.partition("~")
                    self.add_track(title, artist or "The Invented Band", status)
        elif name == "writes":
            self.writes_enabled = query.get("on", "1") == "1"
        elif name == "inject" and query.get("kind"):
            numbers = {k: float(query[k]) for k in ("seconds",) if k in query}
            if "status" in query:
                numbers["status"] = int(query["status"])
            self.inject(query["kind"], query.get("path"), **numbers)
        else:
            return _reply(404, {"detail": "unknown control"})
        return _reply(200, {"ok": True})


def make_handler(fake: FakeGateway) -> type[BaseHTTPRequestHandler]:
    """An ``http.server`` handler over ``fake`` (the e2e server's)."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, reply: Reply) -> None:
            time.sleep(reply.delay)
            if reply.drop:
                self.close_connection = True  # the work is done; no answer
                return
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            if not reply.no_length:
                length = len(reply.body) if reply.declared is None else reply.declared
                self.send_header("Content-Length", str(length))
            self.end_headers()
            if reply.trickle:
                for byte in reply.body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(reply.trickle)
            else:
                self.wfile.write(reply.body)

        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            path, _, query = self.path.partition("?")
            cap = MIX_MAX if path == MERGE_PATH else BODY_MAX
            raw = self.rfile.read(min(length, cap + 1)) if length else b""
            if path.startswith(CONTROL):
                self._send(fake.control(path, dict(parse_qsl(query))))
                return
            headers = {k.lower(): v for k, v in self.headers.items()}
            self._send(fake.handle(method, self.path, headers, raw))

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def log_message(self, format: str, *args: object) -> None:
            return  # no path, body or header in any log

    return Handler
