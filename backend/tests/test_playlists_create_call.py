"""The one request of ``spotify_create_playlist`` (``classbeat.playlists.create_call``, #1287,
roadmap Amendment 30): ``POST me/playlists`` with ``public: true`` and ``collaborative: false``;
an answer without a valid id, or the library's own refusal, is never a refusal by Spotify. The
library's ``call`` is replaced: no network."""

from typing import Any
from urllib.error import HTTPError

import pytest
from mowl import spotify_web
from mowl.errors import MowlError

from classbeat.playlists import create_call

PID = "p" * 21 + "9"


def sent(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake_call(creds: Any, method: str, path: str, params: Any = None, body: Any = None) -> Any:
        calls.append((method, path, params, body))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(spotify_web, "call", fake_call)
    return calls


def test_the_create_asks_for_public_and_not_collaborative(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = sent(monkeypatch, {"id": PID, "public": True})
    assert create_call.create("Class", "classbeat ref abc")(object()) == PID  # type: ignore[arg-type]
    body = {"name": "Class", "description": "classbeat ref abc", "public": True,
            "collaborative": False}  # fmt: skip
    assert calls == [("POST", "me/playlists", None, body)]


@pytest.mark.parametrize("answer", [{}, {"id": "../me"}, {"id": 7}, [], "x", None])
def test_an_answer_without_a_valid_id_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, answer: Any
) -> None:
    sent(monkeypatch, answer)
    with pytest.raises(create_call.AnswerUnreadable):
        create_call.create("Class", "d")(object())  # type: ignore[arg-type]


def test_a_library_refusal_without_an_http_cause_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent(monkeypatch, MowlError("no token"))
    with pytest.raises(create_call.AnswerUnreadable):
        create_call.create("Class", "d")(object())  # type: ignore[arg-type]


def test_an_http_refusal_is_passed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    error = MowlError("Spotify POST me/playlists: HTTP 403", status=403)
    error.__cause__ = HTTPError("https://api.example/", 403, "Forbidden", {}, None)  # type: ignore[arg-type]
    sent(monkeypatch, error)
    with pytest.raises(MowlError):
        create_call.create("Class", "d")(object())  # type: ignore[arg-type]
