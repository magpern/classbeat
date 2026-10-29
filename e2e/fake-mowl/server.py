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

`/__e2e/...` is the test control, reached from the compose network only (the tests call it with
`docker compose exec web`): `arm`, `release` and `state` take `?playlist=<spotify id>`.
"""

import datetime
import json
import os
import re
import ssl
import tempfile
import threading
import uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

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
        if path == "/v1/Spotify/Playlists":
            # The new-class form's playlist list (U1): public test data, any caller.
            listed = [
                {"ID": p.spotify_id, "Name": p.name, "TrackCount": TRACK_COUNT, "HasUnavailableTracks": False}  # noqa: E501
                for p in PLAYLISTS
            ]
            self.ok(listed)
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

    def do_PUT(self) -> None:  # noqa: N802
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
        playlist = BY_SPOTIFY.get(wanted) if isinstance(wanted, str) else None
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

    def read(self, path: str) -> None:
        """The reads of `library.spotify_analysis`; only for a playlist the caller attached."""
        token = self.token()
        if token is None:
            self.reply(401, {"Error": "unauthorized"})
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
        self.ok(tracks())

    def control(self, action: str, query: dict[str, list[str]]) -> None:
        """Test control: `arm` (zero the counters, close the gate), `release` (open the gate) and
        `state` of one playlist."""
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
