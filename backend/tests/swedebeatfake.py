"""The in-process fake Swedebeat for the connect tests (``swedebeatcore``): plain HTTP on
127.0.0.1, with ``mowl.swedebeat.WWW`` and ``CLOUD`` pointed at it for one test. A TEST app key is
made per run (never Swedebeat's) and handed to the app through ``Settings.swedebeat_app``."""

import json
import secrets
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest
from mowl import swedebeat

from swedebeatcore import FakeSwedebeat, make_handler

EMAIL = "rider@example.test"
PASSWORD = "fake-swedebeat-password-Qv7"  # a public test value, searched for in every output
EMAIL_B = "second@example.test"
PASSWORD_B = "fake-swedebeat-password-Bk3"


def make_app_key() -> dict[str, str]:
    """A throwaway app key for this run: 32 and 16 characters. Not Swedebeat's."""
    return {"key": secrets.token_hex(16), "iv": secrets.token_hex(8)}


def app_file_text(app: dict[str, str]) -> str:
    return json.dumps(app)


@pytest.fixture
def swedebeat_app() -> dict[str, str]:
    return make_app_key()


@pytest.fixture
def swedebeat_api(
    monkeypatch: pytest.MonkeyPatch, swedebeat_app: dict[str, str]
) -> Iterator[FakeSwedebeat]:
    fake = FakeSwedebeat(swedebeat_app["key"], swedebeat_app["iv"])
    fake.add_account(EMAIL, PASSWORD, "Rider One")
    fake.add_account(EMAIL_B, PASSWORD_B, "Rider Two")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    monkeypatch.setattr(swedebeat, "WWW", url)
    monkeypatch.setattr(swedebeat, "CLOUD", url)
    # No system or environment proxy between the library and the fake.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(urllib.request, "_opener", opener)
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
