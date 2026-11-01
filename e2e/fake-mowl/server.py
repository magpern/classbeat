"""A fake MOWL API for the E2E stack. TEST ONLY: it runs under the `e2e` compose profile
(compose.e2e.yaml), never by default, and holds no real account or credential.

classbeat's MOWL client is the `mowl-workouts` library, whose base URL `https://api.mowl.com/` is
hard-coded, so this service answers as that host: the compose network alias `api.mowl.com`
points here and the web container trusts a bundle of the fake OIDC provider's CA and this
process's throwaway CA (written to /ca/bundle.pem at start, the health signal). Nothing leaves
the stack.

It speaks the sign-in (`login` in mowl/api.py), the Spotify playlist list (`spotify_playlists`,
for the new-class form, U1) and what the `design_draft` job reads and writes (#94): the attach
(`attach_spotify`) and the three reads of `library.spotify_analysis` (tracks, availability and
sections), over invented music. Authenticate (form Email, Password) gives a login ticket, and the
ticket exchange gives a token that the later calls must present. The account is a public test
value: EMAIL with PASSWORD. Any other pair gets MOWL's own refusal (HTTP 200 with an `Error`). It
never logs a request: no password, ticket or token reaches its output.

Each test playlist has a behaviour (see PLAYLISTS): `ok`; `hold` (its first track read waits until
the test releases it, so a run can be cancelled while it is certainly in flight); `uncertain` (the
first attach of an account is applied but answered with HTTP 500, so classbeat cannot tell).

It also keeps **programs** per account (token), for Save (SV1, `mowl.workout.save`): `PUT
v1/programs`, `PUT v1/segments`, `DELETE v1/segments/<id>/intervals`, `PUT
v1/segments/<id>/intervals/multiple`, `PUT v1/programs/<id>/segments`, and the reads `POST
v1/programs` (My Workouts, newest first, paged) and `GET v1/programs/<id>`; and for Update (SV2)
`PUT v1/programs/<id>`, the re-save in place of the caller's own program (its header; new segment
copies then replace its list). **Failure injection per write request number** (counted from the
last `save-arm`; 1 is the program, then three per segment, then the segment list): `refuse` (not
applied; the connection is closed without an answer), `drop` (applied, then closed without an
answer), `noid` (applied, answered without its id), `copy` (an update's `PUT v1/programs/<id>`
answered as MOWL making a copy: a new, empty program with the header, the original untouched).
`save-edit?program=<id>` changes a program's name as the user would in the MOWL app.

It also speaks the Swedebeat calls (ADR 0016, A1; `swedebeat_mixes.py` beside it): the caller's
mixes, the attach, the indexing and `TemporaryLinks`, whose link names an unlisted media host that no
service answers (the production `MOWL_MEDIA_HOSTS` is empty, so classbeat must refuse it);
`/__e2e/swedebeat-arm` zeroes and `swedebeat-state` shows the counts (never a token or link).

`/__e2e/...` is the test control, reached from the compose network only (the tests call it with
`docker compose exec web`): `arm`, `release` and `state` take `?playlist=<spotify id>`;
`save-arm?fail=<n>:<mode>,...` (zero the write counter and set the injections; no `fail` clears
them; `reset=1` also forgets every program), `save-state` (the counter, the injections, the
number of programs per account, no tokens), `save-listing?hidden=1|0` (My Workouts answers empty,
as if MOWL's listing had not caught up with a write; `save-arm` shows it again) and `save-programs` (each program's account number,
header, segment names and every interval's duration and zone) are for the programs.
"""

import contextlib
import datetime
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import swedebeat_mixes

HOST = "api.mowl.com"
# The constant app ids of mowl/api.py (APP_GUID, TICKET_GUID): part of the sign-in paths.
APP_GUID = "C00869F2-4457-486D-ADAD-47409605B187"
TICKET_GUID = "8285D895-29F6-425B-AE60-1725A68FF42E"
# Public test values, also used by e2e/tests/mowl.spec.ts.
EMAIL = os.environ.get("FAKE_MOWL_EMAIL", "rider@example.test")
PASSWORD = os.environ.get("FAKE_MOWL_PASSWORD", "e2e-fake-mowl-password")
OIDC_CA = os.environ.get("FAKE_MOWL_OIDC_CA", "/oidc-ca/ca.pem")
BUNDLE = os.environ.get("FAKE_MOWL_BUNDLE", "/ca/bundle.pem")
CONTROL = "/__e2e/"

TICKETS: set[str] = set()
# Programs and segments by id, each with the token of the account that wrote it.
PROGRAMS: dict[int, dict[str, object]] = {}
SEGMENTS: dict[int, dict[str, object]] = {}
SAVE = {"writes": 0, "next_program": 800_000, "next_segment": 950_000}
FAIL: dict[int, str] = {}
FAIL_MODES = ("refuse", "drop", "noid", "copy")
LISTING = {"hidden": False}  # My Workouts answers empty: MOWL's listing has not caught up
POSITION_NAMES = {1: "Seated", 2: "Standing"}
CYCLE_NAMES = {1: "SB", 4: "ACC"}
TOKENS: set[str] = set()
LOCK = threading.Lock()
HOLD_SECONDS = 25  # a held read answers after this at the latest (urllib's read timeout is 30 s)


class Playlist:
    """A Spotify playlist as MOWL lists it, and what the fake does when it is used."""

    def __init__(self, spotify_id: str, name: str, mowl_id: int, behaviour: str = "ok") -> None:
        if len(spotify_id) != 22 or not spotify_id.isalnum():
            raise ValueError("a Spotify id has 22 letters and digits")
        self.spotify_id, self.name = spotify_id, name
        self.mowl_id, self.behaviour = mowl_id, behaviour
        self.attaches = 0  # attach calls received, whoever made them
        self.reads: Counter[str] = Counter()  # "tracks", "availability", "sections"
        self.held = 0  # reads waiting at this moment
        self.gate = threading.Event()  # set: held reads go on (and later ones are not held)
        self.owners: set[str] = set()  # tokens that attached it
        self.uncertain_done: set[str] = set()  # tokens whose first attach was answered with a 500
        self.live = False  # wraps a playlist of the fake Spotify: it lists what that holds now


# Public test data. Hill Climb and Sprints are the new-class form's (new-class.spec.ts); the
# others belong to one scenario of design-draft.spec.ts each, so their counters are its own.
PLAYLISTS = [
    Playlist("0aBcDeFgHiJkLmNoPqRsTu", "E2E Hill Climb", 7001),
    Playlist("1zYxWvUtSrQpOnMlKjIhGf", "E2E Sprints", 7002),
    Playlist("2cDeFgHiJkLmNoPqRsTuVw", "E2E Draft Climb", 7003),
    Playlist("3dEfGhIjKlMnOpQrStUvWx", "E2E Slow Mix", 7004, "hold"),
    Playlist("4eFgHiJkLmNoPqRsTuVwXy", "E2E Flaky Attach", 7005, "uncertain"),
]
BY_SPOTIFY = {p.spotify_id: p for p in PLAYLISTS}
BY_MOWL = {p.mowl_id: p for p in PLAYLISTS}
TRACK_COUNT = 8

# Playlists classbeat's song picking makes in the fake Spotify (the pool and the final playlist) and
# attaches here. MOWL is assumed to follow edits of an attached playlist (ADR 0008 section 2), so a
# live playlist lists, on every read, what the fake Spotify's playlist holds now: the fake Spotify
# (backend/tests/spotifycore.py, the code the backend tests run) computes that list, and this fake asks
# its test control. The call is test-only and internal to the compose network: the fake Spotify's
# certificate is not checked here, because the shared CA bundle is written after this service starts.
SPOTIFY_CONTROL = "https://api.spotify.com/__e2e/mowl-tracks?playlist="
FIRST_LIVE_MOWL_ID = 7100
_UNCHECKED = ssl.create_default_context()
_UNCHECKED.check_hostname = False
_UNCHECKED.verify_mode = ssl.CERT_NONE
_DIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_UNCHECKED)
)


def spotify_playlist(spotify_id: str) -> dict[str, object] | None:
    """The fake Spotify's view of a playlist for MOWL (name and tracks), or None if it has none."""
    try:
        with _DIRECT.open(SPOTIFY_CONTROL + spotify_id, timeout=10) as answer:
            found: dict[str, object] = json.load(answer)
            return found
    except urllib.error.HTTPError:
        return None


def live_playlist(spotify_id: str) -> Playlist | None:
    """The fake MOWL playlist wrapping this fake Spotify playlist; made on its first attach."""
    with LOCK:
        known = BY_SPOTIFY.get(spotify_id)
    if known is not None:
        return known
    if len(spotify_id) != 22 or not spotify_id.isalnum():
        return None
    found = spotify_playlist(spotify_id)
    if found is None:
        return None
    with LOCK:
        if spotify_id not in BY_SPOTIFY:
            mowl_id = FIRST_LIVE_MOWL_ID + len(BY_MOWL)
            made = Playlist(spotify_id, str(found["name"]), mowl_id)
            made.live = True
            BY_SPOTIFY[spotify_id], BY_MOWL[mowl_id] = made, made
        return BY_SPOTIFY[spotify_id]


def track_id(i: int) -> str:
    return f"InventedTr{i:012d}"


def tracks() -> list[dict[str, object]]:
    """Eight invented four-minute songs (the shape of backend/tests/agentfake.synthetic_tracks,
    on which the recorded model script good_first_draft.json was written)."""
    return [
        {
            "TrackID": 55501234 + i,
            "Title": f"Invented Song {i}",
            "Artist": f"Made-up Artist {i % 3}",
            "Duration": 240_000,
            "BPM": 120 + 4 * i,
            "SpotifyTrackID": track_id(i),
        }
        for i in range(TRACK_COUNT)
    ]


SECTIONS = [  # a drop and a breakdown in every song
    {"start": 0.0, "duration": 60.0, "loudness": -12.0},
    {"start": 60.0, "duration": 60.0, "loudness": -6.0},
    {"start": 120.0, "duration": 60.0, "loudness": -11.0},
    {"start": 180.0, "duration": 60.0, "loudness": -5.0},
]
SECTION_PATH = re.compile(r"^/v1/Spotify/Tracks/([0-9A-Za-z]{22})/AudioAnalysis$")
PLAYLIST_PATH = re.compile(r"^/v1/Playlists/(\d+)/(tracks|sessiondesigner/availability)$")
# A Swedebeat mix's playlist: its indexing (tracks) and its temporary links (swedebeat_mixes.py).
SWEDEBEAT_READ = re.compile(
    r"^/v1/(?:Playlists/(9\d{3})/tracks|swedebeat/playlist/(9\d{3})/TemporaryLinks)$"
)
PROGRAM_PATH = re.compile(r"^/v1/programs/(\d+)$")
PROGRAM_SEGMENTS_PATH = re.compile(r"^/v1/programs/(\d+)/segments$")
INTERVALS_PATH = re.compile(r"^/v1/segments/(\d+)/intervals$")
INTERVALS_MULTIPLE_PATH = re.compile(r"^/v1/segments/(\d+)/intervals/multiple$")


def is_program_write(method: str, path: str) -> bool:
    if method == "DELETE":
        return INTERVALS_PATH.match(path) is not None
    return method == "PUT" and (
        path in ("/v1/programs", "/v1/segments")
        or PROGRAM_PATH.match(path) is not None
        or PROGRAM_SEGMENTS_PATH.match(path) is not None
        or INTERVALS_MULTIPLE_PATH.match(path) is not None
    )


def apply_write(token: str, method: str, path: str, body: object, copy: bool = False) -> object:
    """One write of `mowl.workout.save`, with the LOCK held; None when the target is unknown."""
    data = body if isinstance(body, dict) else {}
    update = PROGRAM_PATH.match(path)
    if update is not None:
        header = {k: v for k, v in data.items() if k != "ProgramID"}
        if copy:  # MOWL made a new program instead of updating this one
            SAVE["next_program"] += 1
            made = SAVE["next_program"]
            PROGRAMS[made] = {"token": token, "body": header, "segments": []}
            return {"ProgramID": made, "Name": header.get("Name")}
        program = PROGRAMS.get(int(update[1]))
        if program is None or program["token"] != token:
            return None
        program["body"].update(header)  # type: ignore[union-attr]
        return {"ProgramID": int(update[1]), "Name": header.get("Name")}
    if path == "/v1/programs":
        SAVE["next_program"] += 1
        program_id = SAVE["next_program"]
        PROGRAMS[program_id] = {"token": token, "body": dict(data), "segments": []}
        return {"ProgramID": program_id, "Name": data.get("Name")}
    if path == "/v1/segments":
        SAVE["next_segment"] += 1
        segment_id = SAVE["next_segment"]
        SEGMENTS[segment_id] = {"token": token, "body": dict(data), "intervals": []}
        return {"SegmentID": segment_id}
    for pattern, key, target in (
        (PROGRAM_SEGMENTS_PATH, "segments", PROGRAMS),
        (INTERVALS_MULTIPLE_PATH, "intervals", SEGMENTS),
        (INTERVALS_PATH, "intervals", SEGMENTS),
    ):
        found = pattern.match(path)
        if found is None:
            continue
        item = target.get(int(found[1]))
        if item is None or item["token"] != token:
            return None
        if pattern is PROGRAM_SEGMENTS_PATH:
            item[key] = [int(i) for i in data.get("SegmentIDs", [])]
            return {"ProgramID": int(found[1])}
        item[key] = list(data.get("Intervals", [])) if method == "PUT" else []
        return {"Count": len(item[key])}
    return None


def read_program(program_id: int, program: dict[str, object]) -> dict[str, object]:
    """`GET v1/programs/<id>` in the shape `mowl.workout.summarize` reads."""
    body = program["body"]
    assert isinstance(body, dict)
    segments, total = [], 0
    for segment_id in program["segments"]:  # type: ignore[union-attr]
        segment = SEGMENTS[segment_id]
        intervals = []
        for interval in segment["intervals"]:  # type: ignore[union-attr]
            shown = dict(interval)
            shown["PositionTypeName"] = POSITION_NAMES.get(shown.get("PositionTypeID"), "Seated")
            shown["CycleName"] = CYCLE_NAMES.get(shown.get("CycleID"), "SB")
            intervals.append(shown)
        seg_body = segment["body"]
        assert isinstance(seg_body, dict)
        total += int(seg_body.get("TotalDuration") or 0)
        segments.append({"SegmentID": segment_id, **seg_body, "Intervals": intervals})
    return {
        "ProgramID": program_id,
        "Name": body.get("Name"),
        "Description": body.get("Description"),
        "ShortDescription": body.get("ShortDescription"),
        "IsPublic": bool(body.get("IsPublic")),
        "PlaylistID": body.get("PlaylistID"),
        "Playlist": {"PlaylistType": "Spotify", "PlaylistName": "E2E"},
        "TotalDuration": total,
        "TSS": body.get("TSS"),
        "Segments": segments,
    }


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: dict[str, object]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def ok(self, data: object) -> None:
        self.reply(200, {"Data": data, "Error": None})

    def token(self) -> str | None:
        """The caller's session token, if it is one this fake issued."""
        given = self.headers.get("Authorization", "")
        return given if given in TOKENS else None

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path.startswith(CONTROL):
            self.control(path.removeprefix(CONTROL), parse_qs(urlsplit(self.path).query))
            return
        if path == "/v1/programs":
            self.list_programs()
            return
        if path == "/v1/Spotify/Playlists":
            # The new-class form's playlist list (U1): public test data, any caller.
            listed = [
                {"ID": p.spotify_id, "Name": p.name, "TrackCount": TRACK_COUNT, "HasUnavailableTracks": False}  # noqa: E501
                for p in PLAYLISTS
            ]
            self.ok(listed)
            return
        if path == "/v1/swedebeat/Playlists":
            token = self.token()
            if token is None:
                self.reply(401, {"Error": "unauthorized"})
                return
            form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()}
            self.ok(swedebeat_mixes.listing(token, form))
            return
        if path != f"/v1/Authentication/Authenticate/{APP_GUID}":
            self.reply(404, {"Error": "not found"})
            return
        form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()}
        if form.get("Email", "").lower() != EMAIL or form.get("Password") != PASSWORD:
            self.reply(200, {"Data": None, "Error": "Invalid email or password"})
            return
        ticket = str(uuid.uuid4())
        TICKETS.add(ticket)
        self.ok(ticket)

    def do_DELETE(self) -> None:  # noqa: N802
        self.program_write("DELETE", urlsplit(self.path).path)

    def do_PUT(self) -> None:  # noqa: N802
        if is_program_write("PUT", urlsplit(self.path).path):
            self.program_write("PUT", urlsplit(self.path).path)
            return
        if urlsplit(self.path).path == "/v1/swedebeat/Playlist":
            token = self.token()
            if token is None:
                self.reply(401, {"Error": "unauthorized"})
                return
            form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()}
            made = swedebeat_mixes.attach(token, form)
            if made is None:
                self.reply(200, {"Data": None, "Error": "Unknown Swedebeat playlist"})
            else:
                self.ok(made)
            return
        if urlsplit(self.path).path != "/v1/Spotify/Playlist":
            self.reply(404, {"Error": "not found"})
            return
        token = self.token()
        if token is None:
            self.reply(401, {"Error": "unauthorized"})
            return
        try:
            wanted = json.loads(self.body())["SpotifyPlaylistID"]
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"Error": "bad request"})
            return
        playlist = live_playlist(wanted) if isinstance(wanted, str) else None
        if playlist is None:
            self.reply(200, {"Data": None, "Error": "Unknown playlist"})
            return
        with LOCK:
            playlist.attaches += 1
            playlist.owners.add(token)
            lose_answer = playlist.behaviour == "uncertain" and token not in playlist.uncertain_done
            playlist.uncertain_done.add(token)
        if lose_answer:
            # Applied, but classbeat gets a server error: it cannot tell whether it happened.
            self.reply(500, {"Error": "internal error"})
            return
        self.ok({"PlaylistID": playlist.mowl_id, "PlaylistName": playlist.name})

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path.startswith(CONTROL):
            self.control(path.removeprefix(CONTROL), parse_qs(urlsplit(self.path).query))
            return
        parts = path.split("/")
        # /v1/Authentication/Ticket/<ticket>/<TICKET_GUID>
        if len(parts) == 6 and parts[1:4] == ["v1", "Authentication", "Ticket"]:
            if parts[5] == TICKET_GUID and parts[4] in TICKETS:
                TICKETS.discard(parts[4])  # single use
                token = str(uuid.uuid4())
                TOKENS.add(token)
                self.ok(token)
                return
            self.reply(401, {"Error": "unknown ticket"})
            return
        self.read(path)

    def program_write(self, method: str, path: str) -> None:
        """One write of a save, with the failure injected for its number, if any."""
        if not is_program_write(method, path):
            self.reply(404, {"Error": "not found"})
            return
        token = self.token()
        raw = self.body()
        if token is None:
            self.reply(401, {"Error": "unauthorized"})
            return
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            self.reply(400, {"Error": "bad request"})
            return
        with LOCK:
            SAVE["writes"] += 1
            mode = FAIL.get(SAVE["writes"], "ok")
            copy = mode == "copy"
            data = None if mode == "refuse" else apply_write(token, method, path, body, copy)
        if mode in ("refuse", "drop"):
            self.close_connection = True  # no answer: classbeat can't tell what happened
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            return
        if data is None:
            self.reply(200, {"Data": None, "Error": "not found"})
            return
        if mode == "noid" and isinstance(data, dict):
            data = {k: v for k, v in data.items() if not k.endswith("ID")}
        self.ok(data)

    def list_programs(self) -> None:
        """My Workouts of the caller, newest first, one page."""
        token = self.token()
        form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()}
        if token is None:
            self.reply(401, {"Error": "unauthorized"})
            return
        try:
            page, per_page = int(form.get("PageNumber", "0")), int(form.get("itemsPerPage", "50"))
        except ValueError:
            self.reply(400, {"Error": "bad request"})
            return
        with LOCK:
            mine = sorted(
                (i for i, p in PROGRAMS.items() if p["token"] == token and not LISTING["hidden"]),
                reverse=True,
            )
            shown = mine[page * per_page : (page + 1) * per_page]
            listed = [
                {"ProgramID": i, "Name": PROGRAMS[i]["body"].get("Name"), "IsPublic": False}  # type: ignore[union-attr]
                for i in shown
            ]
        self.ok(listed)

    def read(self, path: str) -> None:
        """The reads of `library.spotify_analysis`; only for a playlist the caller attached.
        And `GET v1/programs/<id>`, only for the caller's own program."""
        token = self.token()
        if token is None:
            self.reply(401, {"Error": "unauthorized"})
            return
        mix_read = SWEDEBEAT_READ.match(path)
        if mix_read is not None:
            playlist_id = int(mix_read[1] or mix_read[2])
            read = swedebeat_mixes.tracks if mix_read[1] else swedebeat_mixes.links
            found = read(token, playlist_id)
            if found is None:
                self.reply(404, {"Error": "not found"})
            else:
                self.ok(found)
            return
        program = PROGRAM_PATH.match(path)
        if program is not None:
            with LOCK:
                found = PROGRAMS.get(int(program[1]))
                shown = None if found is None or found["token"] != token else read_program(
                    int(program[1]), found
                )
            if shown is None:
                self.reply(200, {"Data": None, "Error": "No program found"})
            else:
                self.ok(shown)
            return
        if SECTION_PATH.match(path):
            for p in PLAYLISTS:
                if token in p.owners:
                    p.reads["sections"] += 1
            self.ok({"sections": SECTIONS})
            return
        found = PLAYLIST_PATH.match(path)
        playlist = BY_MOWL.get(int(found[1])) if found else None
        if found is None or playlist is None or token not in playlist.owners:
            self.reply(404, {"Error": "not found"})
            return
        if found[2] != "tracks":
            playlist.reads["availability"] += 1
            self.ok({"PlaylistErrors": []})
            return
        playlist.reads["tracks"] += 1
        if playlist.behaviour == "hold" and not playlist.gate.is_set():
            with LOCK:
                playlist.held += 1
            playlist.gate.wait(HOLD_SECONDS)
            with LOCK:
                playlist.held -= 1
        if playlist.live:
            held = spotify_playlist(playlist.spotify_id)
            self.ok(
                [
                    {**e, "PlaylistID": playlist.mowl_id, "Url": "x"}
                    for e in (held["tracks"] if held is not None else [])  # type: ignore[union-attr]
                ]
            )
            return
        self.ok(tracks())

    def control(self, action: str, query: dict[str, list[str]]) -> None:
        """Test control: `arm` (zero the counters, close the gate), `release` (open the gate) and
        `state` of one playlist; `save-arm` and `save-state` for the programs."""
        if action in ("save-arm", "save-state"):
            self.save_control(action, (query.get("fail") or [""])[0], (query.get("reset") or [""])[0])
            return
        if action in ("swedebeat-arm", "swedebeat-state"):
            if action == "swedebeat-arm":
                swedebeat_mixes.reset()
            self.reply(200, swedebeat_mixes.state())
            return
        if action == "save-programs":
            self.save_programs()
            return
        if action == "save-listing":
            LISTING["hidden"] = (query.get("hidden") or [""])[0] == "1"
            self.reply(200, dict(LISTING))
            return
        if action == "save-edit":
            self.save_edit((query.get("program") or [""])[0])
            return
        playlist = BY_SPOTIFY.get((query.get("playlist") or [""])[0])
        if playlist is None:
            self.reply(404, {"Error": "unknown playlist"})
            return
        if action == "arm":
            with LOCK:
                playlist.attaches, playlist.reads = 0, Counter()
                playlist.owners, playlist.uncertain_done = set(), set()
                playlist.gate.clear()
        elif action == "release":
            playlist.gate.set()
        elif action != "state":
            self.reply(404, {"Error": "unknown action"})
            return
        state = {"attaches": playlist.attaches, "held": playlist.held}
        state["reads"] = dict(playlist.reads)
        self.reply(200, state)

    def save_edit(self, program: str) -> None:
        """The user renames a program in the MOWL app (Update then reports `program_changed`)."""
        with LOCK:
            found = PROGRAMS.get(int(program)) if program.isdigit() else None
            if found is not None:
                found["body"]["Name"] = "Renamed in the MOWL app"  # type: ignore[index]
        self.reply(200 if found is not None else 404, {"edited": found is not None})

    def save_programs(self) -> None:
        """What each program holds, for the tests: accounts numbered by their oldest program (no
        tokens), the header, the segment names and every interval's duration and zone."""
        with LOCK:
            number: dict[str, int] = {}
            for program_id in sorted(PROGRAMS):  # an account is numbered by its oldest program
                number.setdefault(str(PROGRAMS[program_id]["token"]), len(number))
            shown = []
            for program_id, program in sorted(PROGRAMS.items()):
                body = program["body"]
                assert isinstance(body, dict)
                segments = [SEGMENTS[i] for i in program["segments"]]  # type: ignore[union-attr]
                shown.append(
                    {
                        "program_id": program_id,
                        "account": number[str(program["token"])],
                        "name": body.get("Name"),
                        "description": body.get("Description"),
                        "public": bool(body.get("IsPublic")),
                        "playlist_id": body.get("PlaylistID"),
                        "segments": [str(s["body"].get("Name")) for s in segments],  # type: ignore[union-attr]
                        "intervals": [
                            [iv.get("Duration"), iv.get("ScaleCoggan")]
                            for s in segments
                            for iv in s["intervals"]  # type: ignore[union-attr]
                        ],
                    }
                )
        self.reply(200, {"programs": shown})

    def save_control(self, action: str, fail: str, reset: str = "") -> None:
        if action == "save-arm":
            wanted: dict[int, str] = {}
            for item in filter(None, fail.split(",")):
                number, _, mode = item.partition(":")
                if not number.isdigit() or mode not in FAIL_MODES:
                    self.reply(400, {"Error": "fail is <n>:<refuse|drop|noid|copy>,..."})
                    return
                wanted[int(number)] = mode
            with LOCK:
                SAVE["writes"] = 0
                FAIL.clear()
                FAIL.update(wanted)
                LISTING["hidden"] = False
                if reset == "1":  # every program and segment is forgotten (a test's clean start)
                    PROGRAMS.clear()
                    SEGMENTS.clear()
        with LOCK:
            accounts = Counter(str(p["token"]) for p in PROGRAMS.values())
            state = {
                "writes": SAVE["writes"],
                "fail": {str(k): v for k, v in sorted(FAIL.items())},
                "programs": sorted(accounts.values()),  # per account, no tokens
            }
        self.reply(200, state)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # no paths (they hold tickets) or bodies in the logs


def make_certificates(directory: str) -> tuple[str, str]:
    """A throwaway CA and a certificate for HOST. Writes the bundle (the OIDC fake's CA plus this
    CA, both public) and returns the server certificate and key paths."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "classbeat e2e fake MOWL CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = os.path.join(directory, "server.pem"), os.path.join(directory, "server.key")
    with open(cert_path, "wb") as handle:
        handle.write(server.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    with open(OIDC_CA, "rb") as handle:
        oidc_ca = handle.read()
    partial = BUNDLE + ".tmp"
    with open(partial, "wb") as handle:
        handle.write(oidc_ca.rstrip(b"\n") + b"\n" + ca.public_bytes(serialization.Encoding.PEM))
    os.replace(partial, BUNDLE)  # appears complete or not at all
    return cert_path, key_path


def main() -> None:
    port = int(os.environ.get("FAKE_MOWL_PORT", "443"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - inside the compose network
    with tempfile.TemporaryDirectory() as directory:
        cert, key = make_certificates(directory)  # also writes BUNDLE: the health signal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
