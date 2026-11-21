"""A fake spotdown for the E2E stack (ADR 0040; #1337). TEST ONLY: it runs under the `e2e` compose
profile (compose.e2e.yaml), never by default, and holds no real key, account or audio. It contacts
nothing.

classbeat's worker reaches spotdown at the origin `CLASSBEAT_SPOTDOWN_URL` names; the stack sets it
to `http://spotdown-api:8000`, a compose network alias that points here (an internal origin, which
`classbeat.spotdown.url_policy` accepts over plain http). It publishes no port. Its one service key
is the PUBLIC TEST value of `service-key.txt` (also mounted into the worker as its
`spotdown_service_key` secret; never a real key).

The fake itself is `backend/tests/spotdowncore.py`, mounted at /fake-core: the same code the backend
tests run in process (`spotdownfake`), so the two cannot drift. It answers `/v2/match`,
`/v2/analyses`, `/v2/jobs/{id}` and `/v2/analyses/{id}`. Every `youtube` video it names carries an
analysis document (`songanalysisdocs.youtube_doc`) whose tempo is the fake MOWL's tempo of the
Spotify track whose title it was asked for: the fake Spotify's titles end in the track's catalogue
number, so the document agrees with MOWL and the song's timing is "approximate", as it would for a
real YouTube analysis. It never logs a request: no title, source or key reaches its output.

`/__e2e/...` is the test control, reached from the compose network only (no key): `state` (every
request's method and path, no header, body or key) and `reset`.
"""

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

sys.path.insert(0, os.environ.get("FAKE_SPOTDOWN_CORE", "/fake-core"))
import songanalysisdocs
import spotdowncore
import spotifycore

KEY_FILE = os.environ.get("FAKE_SPOTDOWN_KEY", "/fake/service-key.txt")
CONTROL = "/__e2e/"
BODY_MAX = 1_000_000
NUMBER = re.compile(r"(\d{1,3})$")  # the catalogue number a fake Spotify title ends in


class E2eSpotdown(spotdowncore.FakeSpotdown):
    """The core, with a video per Spotify track (the same in every batch) and its document."""

    def video(self, kind: str, n: int, track: dict[str, object]) -> str:
        found = NUMBER.search(str(track.get("title", "")))
        index = int(found[1]) if found else n
        video = f"{kind}{index:08d}"
        tempo = float(spotifycore.mowl_bpm(spotifycore.track_id(index)))
        self.documents[f"youtube:{video}"] = songanalysisdocs.youtube_doc(video, tempo)
        return video


class State:
    def __init__(self) -> None:
        with open(KEY_FILE, encoding="utf-8") as handle:
            self.key = handle.read().strip()
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.fake = E2eSpotdown(key=self.key)
        self.requests: list[dict[str, str]] = []


def make_handler(state: State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                if name.lower() in ("content-type", "retry-after"):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: object) -> None:
            self._send(status, {"content-type": "application/json"}, json.dumps(value).encode())

        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, BODY_MAX + 1)) if length else b""
            path = self.path.partition("?")[0]
            if path.startswith(CONTROL):
                self._control(method, path.removeprefix(CONTROL))
                return
            request = httpx.Request(
                method, f"http://spotdown-api{self.path}", headers=dict(self.headers), content=body
            )
            with state.lock:
                state.requests.append({"method": method, "path": path})
                answer = state.fake.handle(request, body)
            self._send(answer.status_code, dict(answer.headers), answer.content)

        def _control(self, method: str, name: str) -> None:
            with state.lock:
                if name == "reset" and method == "POST":
                    state.reset()
                    self._json(200, {"ok": True})
                elif name == "state" and method == "GET":
                    self._json(200, {"requests": state.requests})
                else:
                    self._json(404, {"error": "unknown control"})

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def do_PUT(self) -> None:
            self._serve("PUT")

        def do_DELETE(self) -> None:
            self._serve("DELETE")

        def log_message(self, format: str, *args: object) -> None:
            return  # no path, body or header in any log

    return Handler


def main() -> None:
    port = int(os.environ.get("FAKE_SPOTDOWN_PORT", "8000"))
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),  # noqa: S104 - inside the compose network, no published port
        make_handler(State()),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
