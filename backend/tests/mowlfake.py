"""An in-process fake of the MOWL REST API for the connect tests. Never the network.

It speaks the two sign-in calls ``mowl.api.login`` makes (Authenticate → a login ticket, then the
ticket exchange → the token) and ``POST v1/programs`` (``list_workouts``), on 127.0.0.1 over plain
HTTP; the ``mowl_api`` fixture points ``mowl.api.API`` at it for one test. Public test values only.

It also speaks the Swedebeat calls of the library (ADR 0016, A1): ``POST v1/swedebeat/Playlists``
(the caller's own mixes), ``PUT v1/swedebeat/Playlist`` (attach: a MOWL playlist, the same one on a
repeat), ``GET v1/Playlists/{id}/tracks`` (the indexing, which can take several polls or fail) and
``GET v1/swedebeat/playlist/{id}/TemporaryLinks`` (a link on the fake media host, ``mediafake``,
whose behaviour is chosen per mix). Mixes belong to a token (``add_mix``); another token never
lists,
attaches or fetches them. The same mix id can belong to two tokens (``add_mix(mix_id=...)``): each
token has its own playlist for it.

A2 additions: failures per endpoint (``status["swedebeat_list"|"swedebeat_attach"|
"swedebeat_tracks"|"swedebeat_links"]``, or ``"swedebeat"`` for all four; 401, 429 with
``retry_after``, 5xx); ``attach_answer`` (``"ok"``, ``"drop"``: the attach happens and the
connection is cut before the answer, ``"garbled"``: it happens and the answer has no
``PlaylistID``); and the listing shows the ``PlaylistID`` of every mix the caller has attached
(``list_shows_playlist``), which is what the attach reconciler reads; ``list_ok_calls`` lets the
listing fail after that many calls.
"""

import json
import threading
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

import pytest
from mowl import api

from mediafake import FakeMedia

EMAIL = "rider@example.com"
PASSWORD = "fake-mowl-password-Zq8"  # a public test value, searched for in every output
TICKET = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN = "abababab-cdcd-efef-0101-232323232323"
AUTHENTICATE = f"/v1/Authentication/Authenticate/{api.APP_GUID}"
EXCHANGE = f"/v1/Authentication/Ticket/{TICKET}/{api.TICKET_GUID}"


DROP = -1  # an answer code meaning: cut the connection without answering


@dataclass
class Mix:
    id: str
    name: str
    listed_ms: int
    audio_seconds: float
    link: str = "ok"  # a mediafake behaviour
    link_options: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    index_polls: int = 0  # track reads answered "still indexing" before it is done
    index_error: str | None = None
    playlist_id: dict[str, int] = field(default_factory=lambda: dict[str, int]())  # per token


@dataclass
class FakeMowl:
    url: str = ""
    token: str = TOKEN
    # Status to answer per path ("authenticate", "exchange", "programs", and the Swedebeat
    # endpoints, see the module docstring); 200 when absent.
    status: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    retry_after: str | None = None
    requests: list[str] = field(default_factory=lambda: list[str]())
    passwords_seen: list[str] = field(default_factory=lambda: list[str]())
    mixes: dict[str, list[Mix]] = field(default_factory=lambda: dict[str, list[Mix]]())
    media: FakeMedia = field(default_factory=FakeMedia)
    attaches: list[str] = field(default_factory=lambda: list[str]())  # mix ids, in order
    link_requests: int = 0
    attach_answer: str = "ok"
    list_shows_playlist: bool = True
    list_ok_calls: int | None = None  # the listing answers 503 once this many calls were made
    list_calls: int = 0
    polls: dict[int, int] = field(default_factory=lambda: dict[int, int]())
    _next_playlist: int = 9000

    def add_mix(
        self,
        name: str,
        *,
        token: str = TOKEN,
        listed_seconds: float = 120.0,
        audio_seconds: float | None = None,
        link: str = "ok",
        index_polls: int = 0,
        index_error: str | None = None,
        mix_id: str | None = None,
        **link_options: Any,
    ) -> str:
        """A Swedebeat mix of ``token``'s account; ``audio_seconds`` is what its file holds.
        ``mix_id`` names it (the same id under two tokens is two mixes of two accounts)."""
        mix_id = mix_id or (
            f"{len(self.mixes.get(token, [])) + 1:08d}-mix-{abs(hash((token, name))) % 10**6:06d}"
        )
        listed = Mix(
            mix_id, name, int(listed_seconds * 1000),
            listed_seconds if audio_seconds is None else audio_seconds,
            link, link_options, index_polls, index_error,
        )  # fmt: skip
        self.mixes.setdefault(token, []).append(listed)
        return mix_id

    def _own(self, token: str) -> list[Mix]:
        return self.mixes.get(token, [])

    def _swedebeat(
        self, method: str, path: str, form: dict[str, str], token: str
    ) -> tuple[int, Any] | None:
        if method == "POST" and path == "/v1/swedebeat/Playlists":
            like = form.get("NameLike", "").lower()
            listed: list[dict[str, Any]] = []
            for m in self._own(token):
                if like not in m.name.lower():
                    continue
                entry: dict[str, Any] = {
                    "ID": m.id,
                    "Name": m.name,
                    "URL": f"mix:{m.id}",
                    "DurationInMilliseconds": m.listed_ms,
                }
                if self.list_shows_playlist and token in m.playlist_id:
                    entry["PlaylistID"] = m.playlist_id[token]
                listed.append(entry)
            return 200, {"Data": {"Playlists": listed}, "Error": None}
        if method == "PUT" and path == "/v1/swedebeat/Playlist":
            found = next(
                (m for m in self._own(token) if m.id == form.get("SwedebeatPlaylistID")), None
            )
            if found is None:
                return 200, {"Data": None, "Error": "Unknown Swedebeat playlist"}
            if token not in found.playlist_id:
                self._next_playlist += 1
                found.playlist_id[token] = self._next_playlist
            self.attaches.append(found.id)
            if self.attach_answer == "drop":
                return DROP, None
            if self.attach_answer == "garbled":
                return 200, {"Data": {"PlaylistName": found.name}, "Error": None}
            return 200, {
                "Data": {"PlaylistID": found.playlist_id[token], "PlaylistName": found.name},
                "Error": None,
            }
        parts = path.split("/")
        if (
            method == "GET"
            and len(parts) == 5
            and parts[1:2] == ["v1"]
            and parts[2] == "Playlists"
            and parts[4] == "tracks"
        ):
            mix = self._attached(token, parts[3])
            if mix is None:
                return 404, {"Error": "not found"}
            n = self.polls[mix.playlist_id[token]] = self.polls.get(mix.playlist_id[token], 0) + 1
            track: dict[str, Any] = {
                "TrackID": 70_000_000 + mix.playlist_id[token],
                "Duration": int(mix.audio_seconds * 1000),
                "IndexStatus": "Track indexing done",
                "IndexError": None,
            }
            if mix.index_error is not None:
                track.update(IndexStatus="Track indexing failed", IndexError=mix.index_error)
            elif n <= mix.index_polls:
                track.update(IndexStatus="Track indexing in progress")
            return 200, {"Data": [track], "Error": None}
        if (
            method == "GET"
            and len(parts) == 6
            and parts[2] == "swedebeat"
            and parts[5] == "TemporaryLinks"
        ):
            mix = self._attached(token, parts[4])
            if mix is None:
                return 404, {"Error": "not found"}
            self.link_requests += 1
            options = {"seconds": mix.audio_seconds, **mix.link_options}
            return 200, {
                "Data": {
                    "TemporaryLinks": [{"TemporaryLink": self.media.link(mix.link, **options)}]
                },
                "Error": None,
            }
        return None

    @staticmethod
    def _endpoint(method: str, path: str) -> str:
        """The name a failure switch uses: list, attach, tracks or links."""
        if path == "/v1/swedebeat/Playlists":
            return "list"
        if path == "/v1/swedebeat/Playlist":
            return "attach"
        return "links" if path.endswith("/TemporaryLinks") else "tracks"

    def _attached(self, token: str, playlist: str) -> Mix | None:
        return next(
            (m for m in self._own(token) if str(m.playlist_id.get(token)) == playlist), None
        )

    def answer(
        self, path: str, form: dict[str, str], authorization: str | None, method: str = "POST"
    ) -> tuple[int, Any]:
        if path.startswith(("/v1/swedebeat/", "/v1/Playlists/")):
            if authorization != self.token and authorization not in self.mixes:
                return 401, {"Error": "unauthorized"}
            endpoint = self._endpoint(method, path)
            if endpoint == "list":
                self.list_calls += 1
                if self.list_ok_calls is not None and self.list_calls > self.list_ok_calls:
                    return 503, {"Error": "no"}
            code = self.status.get(f"swedebeat_{endpoint}") or self.status.get("swedebeat", 200)
            if code != 200:
                return code, {"Error": "no"}
            if (answered := self._swedebeat(method, path, form, authorization or "")) is not None:
                return answered
        if path == AUTHENTICATE:
            self.passwords_seen.append(form.get("Password", ""))
            if (code := self.status.get("authenticate", 200)) != 200:
                return code, {"Error": f"refused {form.get('Password')}"}
            if form.get("Email") != EMAIL or form.get("Password") != PASSWORD:
                # MOWL's own refusal: HTTP 200 with an error, no ticket.
                return 200, {"Data": None, "Error": f"Wrong password {form.get('Password')}"}
            return 200, {"Data": TICKET, "Error": None}
        if path == EXCHANGE:
            if (code := self.status.get("exchange", 200)) != 200:
                return code, {"Error": f"ticket {TICKET} refused"}
            return 200, {"Data": self.token, "Error": None}
        if path == "/v1/programs":
            if authorization != self.token:
                return 401, {"Error": "unauthorized"}
            if (code := self.status.get("programs", 200)) != 200:
                return code, {"Error": "no"}
            return 200, {"Data": [], "Error": None}
        return 404, {"Error": "not found"}


def _handler(fake: FakeMowl) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode() if length else ""
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            path = self.path.split("?")[0]
            fake.requests.append(f"{self.command} {path}")
            status, body = fake.answer(path, form, self.headers.get("authorization"), self.command)
            if status == DROP:
                self.close_connection = True
                return
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 429 and fake.retry_after is not None:
                self.send_header("Retry-After", fake.retry_after)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = _serve

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@pytest.fixture
def mowl_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMowl]:
    fake = FakeMowl()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}/"
    monkeypatch.setattr(api, "API", fake.url)
    # No system or environment proxy between the library and the fake.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(urllib.request, "_opener", opener)
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
