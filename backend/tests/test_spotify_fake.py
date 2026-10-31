"""The fake Spotify itself (ADR 0009 §13): PKCE, refresh rotation, pagination in both item
shapes, every failure injection, and what it records. Through the real allowlisted clients
(``spotifyfake``) with the fake as their network; never the network."""

import http.client
import json
import secrets
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from classbeat.egress import RequestDeadline
from spotifycore import (
    NOT_ALLOWLISTED,
    SCOPES,
    UNLISTED_HOST,
    FakeSpotify,
    challenge_of,
    track_id,
)
from spotifyfake import (
    CALLBACK,
    CLIENT_A,
    CLIENT_B,
    USER_A,
    USER_B,
    spotify_client,
    standard,
    worker_opener,
)

ACCOUNTS = "https://accounts.spotify.com"
API = "https://api.spotify.com/v1"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _verifier() -> str:
    return secrets.token_urlsafe(32)  # 43 characters, as classbeat makes it


def _authorize_url(client_id: str, verifier: str, state: str, **extra: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": CALLBACK,
        "scope": SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge_of(verifier),
        "show_dialog": "true",
        **extra,
    }
    return f"{ACCOUNTS}/authorize?{urlencode(params)}"


def _approve(fake: FakeSpotify, client_id: str, verifier: str, state: str = "st") -> dict[str, str]:
    """The browser's visit to the authorize page: the redirect's query."""
    answer = fake.handle("GET", _authorize_url(client_id, verifier, state), {}, None)
    assert answer.status == 302
    location = answer.headers["Location"]
    assert location.startswith(CALLBACK + "?")
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def _token(
    client: httpx.AsyncClient, form: dict[str, str], headers: dict[str, str] | None = None
) -> httpx.Response:
    return await client.post(f"{ACCOUNTS}/api/token", data=form, headers=headers)


async def _exchange(
    client: httpx.AsyncClient,
    code: str,
    verifier: str,
    client_id: str = CLIENT_A,
    over: dict[str, str] | None = None,
) -> httpx.Response:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "client_id": client_id,
        "code_verifier": verifier,
        **(over or {}),
    }
    return await _token(client, form)


async def _connect(fake: FakeSpotify, client_id: str = CLIENT_A) -> dict[str, Any]:
    verifier = _verifier()
    code = _approve(fake, client_id, verifier)["code"]
    async with spotify_client(fake) as client:
        answer = await _exchange(client, code, verifier, client_id)
    assert answer.status_code == 200
    return answer.json()


def _open(fake: FakeSpotify, url: str, token: str, method: str = "GET", body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310 - https only, under the allowlist
        url, data=data, method=method, headers={"Authorization": f"Bearer {token}"}
    )
    with worker_opener(fake).open(request, timeout=5) as response:
        return json.loads(response.read())


def _error(fake: FakeSpotify, url: str, token: str, method: str = "GET", body: Any = None) -> Any:
    with pytest.raises(urllib.error.HTTPError) as raised:
        _open(fake, url, token, method, body)
    return raised.value


# --- PKCE -----------------------------------------------------------------------------------


async def test_pkce_success_issues_tokens_for_the_signed_in_user_and_app(
    spotify: FakeSpotify,
) -> None:
    verifier = _verifier()
    query = _approve(spotify, CLIENT_A, verifier, state="planted-state")
    assert query["state"] == "planted-state"
    async with spotify_client(spotify) as client:
        answer = await _exchange(client, query["code"], verifier)
        assert answer.status_code == 200
        tokens = answer.json()
        assert tokens["token_type"] == "Bearer"
        assert tokens["scope"] == SCOPES
        assert tokens["expires_in"] == 3600
        assert tokens["refresh_token"]
        me = await client.get(
            f"{API}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
    assert me.status_code == 200
    assert me.json()["id"] == USER_A
    token_request = spotify.requests(path="/api/token")[0]
    assert (token_request.client_id, token_request.client_secret) == (CLIENT_A, False)
    me_request = spotify.requests(path="/v1/me")[0]
    assert me_request.authorization == f"Bearer {tokens['access_token']}"
    assert (me_request.token_app, me_request.token_user) == (CLIENT_A, USER_A)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"code_verifier": "x" * 43}, "invalid_grant"),  # another verifier
        ({"code_verifier": ""}, "invalid_grant"),
        ({"code_verifier": "short"}, "invalid_grant"),
        ({"redirect_uri": "http://127.0.0.1:9999/oauth/spotify/callback"}, "invalid_grant"),
        ({"code": "fake-code-unknown"}, "invalid_grant"),
        ({"client_id": CLIENT_B}, "invalid_grant"),  # the code was issued to app A
        ({"client_id": "c" * 32}, "invalid_client"),  # no such app
        ({"grant_type": "password"}, "unsupported_grant_type"),
    ],
)
async def test_pkce_failures(spotify: FakeSpotify, change: dict[str, str], error: str) -> None:
    verifier = _verifier()
    code = _approve(spotify, CLIENT_A, verifier)["code"]
    async with spotify_client(spotify) as client:
        answer = await _exchange(client, code, verifier, over=change)
    assert answer.status_code == 400
    assert answer.json()["error"] == error


async def test_a_code_is_single_use_and_dies_with_a_failed_attempt(spotify: FakeSpotify) -> None:
    verifier = _verifier()
    code = _approve(spotify, CLIENT_A, verifier)["code"]
    async with spotify_client(spotify) as client:
        assert (await _exchange(client, code, verifier)).status_code == 200
        assert (await _exchange(client, code, verifier)).json()["error"] == "invalid_grant"
        second = _approve(spotify, CLIENT_A, verifier)["code"]
        assert (await _exchange(client, second, "y" * 43)).json()["error"] == "invalid_grant"
        assert (await _exchange(client, second, verifier)).json()["error"] == "invalid_grant"


async def test_an_expired_code_is_refused() -> None:
    now = [1_000_000.0]
    fake = standard(FakeSpotify(clock=lambda: now[0]))
    verifier = _verifier()
    code = _approve(fake, CLIENT_A, verifier)["code"]
    now[0] += 601
    async with spotify_client(fake) as client:
        assert (await _exchange(client, code, verifier)).json()["error"] == "invalid_grant"


@pytest.mark.parametrize(
    "secret",
    [
        {"form": {"client_secret": "planted-secret"}},
        {"headers": {"Authorization": "Basic cGxhbnRlZDpzZWNyZXQ="}},
    ],
)
async def test_a_client_secret_is_recorded_and_refused(
    spotify: FakeSpotify, secret: dict[str, dict[str, str]]
) -> None:
    verifier = _verifier()
    code = _approve(spotify, CLIENT_A, verifier)["code"]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "client_id": CLIENT_A,
        "code_verifier": verifier,
        **secret.get("form", {}),
    }
    async with spotify_client(spotify) as client:
        answer = await _token(client, form, secret.get("headers"))
    assert (answer.status_code, answer.json()["error"]) == (400, "invalid_request")
    assert [s.client_secret for s in spotify.requests(path="/api/token")] == [True]


def test_the_authorize_page_checks_the_app_and_the_redirect(spotify: FakeSpotify) -> None:
    verifier = _verifier()
    unknown = spotify.handle("GET", _authorize_url("c" * 32, verifier, "s"), {}, None)
    assert (unknown.status, "Location" in unknown.headers) == (400, False)
    other = _authorize_url(CLIENT_A, verifier, "s", redirect_uri="https://evil.example/cb")
    answer = spotify.handle("GET", other, {}, None)
    assert (answer.status, "Location" in answer.headers) == (400, False)
    plain = _authorize_url(CLIENT_A, verifier, "s", code_challenge_method="plain")
    answer = spotify.handle("GET", plain, {}, None)
    assert parse_qs(urlsplit(answer.headers["Location"]).query) == {
        "error": ["invalid_request"],
        "state": ["s"],
    }
    assert spotify.requests()[0].client_id == "c" * 32


def test_a_cancel_redirects_with_access_denied_and_the_state(spotify: FakeSpotify) -> None:
    spotify.deny()
    answer = spotify.handle("GET", _authorize_url(CLIENT_A, _verifier(), "st-1"), {}, None)
    query = parse_qs(urlsplit(answer.headers["Location"]).query)
    assert query == {"error": ["access_denied"], "state": ["st-1"]}
    assert spotify.codes == {}


# --- refresh --------------------------------------------------------------------------------


async def test_refresh_rotates_and_the_old_refresh_token_dies(spotify: FakeSpotify) -> None:
    first = await _connect(spotify)
    async with spotify_client(spotify) as client:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": CLIENT_A,
        }
        answer = await _token(client, form)
        assert answer.status_code == 200
        second = answer.json()
        assert second["refresh_token"] != first["refresh_token"]
        assert second["access_token"] != first["access_token"]
        again = await _token(client, form)
        assert (again.status_code, again.json()["error"]) == (400, "invalid_grant")
        form["refresh_token"] = second["refresh_token"]
        assert (await _token(client, form)).status_code == 200
    assert [s.client_id for s in spotify.requests(path="/api/token")] == [CLIENT_A] * 4


async def test_refresh_without_rotation_keeps_the_refresh_token(spotify: FakeSpotify) -> None:
    spotify.apps[CLIENT_A].rotate_refresh = False
    first = await _connect(spotify)
    form = {"grant_type": "refresh_token", "refresh_token": first["refresh_token"]}
    async with spotify_client(spotify) as client:
        answer = await _token(client, {**form, "client_id": CLIENT_A})
        assert "refresh_token" not in answer.json()
        assert (await _token(client, {**form, "client_id": CLIENT_A})).status_code == 200
        # Another app's id with this refresh token: never accepted.
        other = await _token(client, {**form, "client_id": CLIENT_B})
        assert other.json()["error"] == "invalid_grant"


async def test_an_expired_access_token_answers_401(spotify: FakeSpotify) -> None:
    tokens = await _connect(spotify)
    spotify.expire(tokens["access_token"])
    error = _error(spotify, f"{API}/me", tokens["access_token"])
    assert error.code == 401


# --- the API --------------------------------------------------------------------------------


def test_a_user_missing_from_the_allow_list_gets_403_at_the_first_me(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_B)  # B is on app B's list, not app A's
    error = _error(spotify, f"{API}/me", access)
    assert error.code == 403
    assert json.loads(error.read())["error"]["message"] == NOT_ALLOWLISTED


def test_an_owner_without_premium_gets_403(spotify: FakeSpotify) -> None:
    spotify.apps[CLIENT_A].owner_premium = False
    access, _ = spotify.grant(CLIENT_A, USER_A)
    assert _error(spotify, f"{API}/me", access).code == 403


def test_search_pages_of_at_most_ten(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    found = _open(spotify, f"{API}/search?type=track&q=climb&limit=10", access)["tracks"]
    assert len(found["items"]) == 10
    assert found["total"] > 10
    assert all("climb" in t["name"].lower() for t in found["items"])
    later = _open(spotify, f"{API}/search?type=track&q=climb&limit=10&offset=10", access)
    assert later["tracks"]["items"][0]["id"] != found["items"][0]["id"]
    assert _error(spotify, f"{API}/search?type=track&q=climb&limit=11", access).code == 400
    assert _error(spotify, f"{API}/search?type=track&q=", access).code == 400


def test_create_is_public_unless_told_otherwise(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    omitted = _open(spotify, f"{API}/me/playlists", access, "POST", {"name": "n"})
    assert omitted["public"] is True  # Spotify's default: classbeat must send false
    private = {"name": "p", "public": False, "collaborative": False, "description": "d"}
    created = _open(spotify, f"{API}/me/playlists", access, "POST", private)
    assert (created["public"], created["collaborative"], created["owner"]["id"]) == (
        False,
        False,
        USER_A,
    )
    assert created["items"]["total"] == 0
    both = {"name": "x", "public": True, "collaborative": True}
    assert _error(spotify, f"{API}/me/playlists", access, "POST", both).code == 400


def _filled(fake: FakeSpotify, count: int, owner: str = USER_A) -> str:
    uris = [f"spotify:track:{track_id(i)}" for i in range(count)]
    return fake.add_playlist(owner, "Filled", uris).id


@pytest.mark.parametrize("shape", ["item", "track"])
def test_items_paginate_120_in_pages_of_50(spotify: FakeSpotify, shape: str) -> None:
    spotify.item_shape = shape
    access, _ = spotify.grant(CLIENT_A, USER_A)
    playlist = _filled(spotify, 120)
    ids: list[str] = []
    sizes: list[int] = []
    offset = 0
    while True:
        page = _open(spotify, f"{API}/playlists/{playlist}/items?limit=50&offset={offset}", access)
        assert page["total"] == 120
        if not page["items"]:
            break
        sizes.append(len(page["items"]))
        for entry in page["items"]:
            assert set(entry) >= {shape, "is_local"}
            assert ({"item", "track"} - {shape}).isdisjoint(entry)
            ids.append(entry[shape]["id"])
        offset += 50
        if offset >= page["total"]:
            break
    assert sizes == [50, 50, 20]
    assert ids == [track_id(i) for i in range(120)]
    assert _error(spotify, f"{API}/playlists/{playlist}/items?limit=51", access).code == 400


def test_writes_replace_and_append_at_most_100(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    playlist = _filled(spotify, 3)
    url = f"{API}/playlists/{playlist}/items"
    uris = [f"spotify:track:{track_id(i)}" for i in range(10, 110)]
    put = _open(spotify, url, access, "PUT", {"uris": uris[:100]})
    assert put["snapshot_id"]
    _open(spotify, url, access, "POST", {"uris": [f"spotify:track:{track_id(150)}"]})
    assert spotify.playlists[playlist].entries == [*uris[:100], f"spotify:track:{track_id(150)}"]
    assert _error(spotify, url, access, "PUT", {"uris": uris + uris[:1]}).code == 400
    assert _error(spotify, url, access, "PUT", {"uris": ["spotify:track:bad"]}).code == 400
    _open(spotify, url, access, "PUT", {"uris": []})  # an empty list clears
    assert spotify.playlists[playlist].entries == []


def test_another_users_playlist_is_404_or_closed(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    theirs = _filled(spotify, 2, owner=USER_B)
    for path in ("", "/items"):
        assert _error(spotify, f"{API}/playlists/{theirs}{path}", access).code == 404
    spotify.foreign = "visible"
    meta = _open(spotify, f"{API}/playlists/{theirs}", access)
    assert meta["owner"]["id"] == USER_B
    assert "total" not in meta["items"]
    assert _error(spotify, f"{API}/playlists/{theirs}/items", access).code == 403
    write = {"uris": [f"spotify:track:{track_id(1)}"]}
    assert _error(spotify, f"{API}/playlists/{theirs}/items", access, "PUT", write).code == 403
    assert len(spotify.playlists[theirs].entries) == 2


def test_my_playlists_lists_only_mine_newest_first(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    old = _filled(spotify, 1)
    _filled(spotify, 1, owner=USER_B)
    new = _filled(spotify, 1)
    page = _open(spotify, f"{API}/me/playlists?limit=50", access)
    assert [p["id"] for p in page["items"]] == [new, old]
    assert _error(spotify, f"{API}/me/playlists?limit=51", access).code == 400


def test_the_old_tracks_path_and_ids_are_checked(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    playlist = _filled(spotify, 1)
    assert _error(spotify, f"{API}/playlists/{playlist}/tracks", access).code == 404
    assert _error(spotify, f"{API}/playlists/not-an-id/items", access).code == 400
    assert _error(spotify, f"{API}/playlists/{'Z' * 22}", access).code == 404


def test_the_same_user_on_two_apps_sees_the_same_playlists(spotify: FakeSpotify) -> None:
    spotify.apps[CLIENT_B].allowed.add(USER_A)
    playlist = _filled(spotify, 1)
    a, _ = spotify.grant(CLIENT_A, USER_A)
    b, _ = spotify.grant(CLIENT_B, USER_A)
    assert _open(spotify, f"{API}/playlists/{playlist}", a)["id"] == playlist
    assert _open(spotify, f"{API}/playlists/{playlist}", b)["id"] == playlist
    apps = [s.token_app for s in spotify.requests(path=f"/v1/playlists/{playlist}")]
    assert apps == [CLIENT_A, CLIENT_B]


# --- failure injection ----------------------------------------------------------------------


def _create(fake: FakeSpotify, access: str) -> Any:
    body = {"name": "pool", "public": False, "collaborative": False}
    return _open(fake, f"{API}/me/playlists", access, "POST", body)


async def test_refuse_fails_before_anything_is_applied(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("refuse", method="POST", path="/v1/me/playlists", times=2)
    with pytest.raises(urllib.error.URLError) as raised:
        _create(spotify, access)
    assert isinstance(raised.value.reason, ConnectionRefusedError)
    async with spotify_client(spotify) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(
                f"{API}/me/playlists",
                json={"name": "x"},
                headers={"Authorization": f"Bearer {access}"},
            )
    assert spotify.playlists == {}
    assert [s.outcome for s in spotify.requests()] == ["refused", "refused"]


async def test_drop_applies_the_effect_and_loses_the_answer(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("drop", path="/v1/me/playlists", times=2)
    with pytest.raises(http.client.RemoteDisconnected):
        _create(spotify, access)
    async with spotify_client(spotify) as client:
        with pytest.raises(httpx.RemoteProtocolError):
            await client.post(
                f"{API}/me/playlists",
                json={"name": "y", "public": False},
                headers={"Authorization": f"Bearer {access}"},
            )
    assert [p.name for p in spotify.playlists.values()] == ["pool", "y"]
    assert [s.outcome for s in spotify.requests()] == ["dropped", "dropped"]


def test_noid_applies_and_answers_without_an_id(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("noid", path="/v1/me/playlists")
    created = _create(spotify, access)
    assert "id" not in created
    assert "snapshot_id" not in created
    assert len(spotify.playlists) == 1
    assert "id" in _create(spotify, access)  # once only


@pytest.mark.parametrize("kind", ["invalid_grant", "invalid_client"])
async def test_token_endpoint_errors(spotify: FakeSpotify, kind: str) -> None:
    _, refresh = spotify.grant(CLIENT_A, USER_A)
    spotify.inject(kind, path="/api/token")
    form = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_A}
    async with spotify_client(spotify) as client:
        answer = await _token(client, form)
        assert (answer.status_code, answer.json()["error"]) == (400, kind)
        assert (await _token(client, form)).status_code == 200  # the token was not touched


@pytest.mark.parametrize("status", [401, 403, 404, 500, 502, 503])
def test_status_injection(spotify: FakeSpotify, status: int) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("status", status=status, path="/v1/me", message="Forbidden")
    error = _error(spotify, f"{API}/me", access)
    assert error.code == status
    assert json.loads(error.read())["error"]["status"] == status
    assert _open(spotify, f"{API}/me", access)["id"] == USER_A
    first = spotify.requests()[0]
    assert (first.token_app, first.token_user, first.status) == (CLIENT_A, USER_A, status)


@pytest.mark.parametrize("seconds", [5, 3601, 7200])
def test_rate_limited_carries_retry_after(spotify: FakeSpotify, seconds: int) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("rate_limited", retry_after=seconds)
    error = _error(spotify, f"{API}/me", access)
    assert error.code == 429
    assert error.headers["Retry-After"] == str(seconds)


def test_an_injection_by_request_number(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("status", at=2, status=500)
    _open(spotify, f"{API}/me", access)
    assert _error(spotify, f"{API}/me", access).code == 500
    _open(spotify, f"{API}/me", access)
    assert [s.number for s in spotify.requests()] == [1, 2, 3]


def test_trickle_answers_one_byte_at_a_time_over_urllib(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("trickle", path="/v1/me", delay=0.002)
    started = time.monotonic()
    me = _open(spotify, f"{API}/me", access)
    length = len(json.dumps(me))
    assert me["id"] == USER_A
    assert time.monotonic() - started >= length * 0.002 * 0.8


async def test_trickle_is_cut_by_the_spotify_clients_total_deadline(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    spotify.inject("trickle", path="/v1/me", delay=0.05)
    started = time.monotonic()
    async with spotify_client(spotify, total_seconds=0.3) as client:
        with pytest.raises(RequestDeadline):
            await client.get(f"{API}/me", headers={"Authorization": f"Bearer {access}"})
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("kind", ["local", "episode", "null", "duplicate"])
def test_unplayable_and_duplicate_entries(spotify: FakeSpotify, kind: str) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    playlist = _filled(spotify, 3)
    spotify.inject(kind, path=f"/v1/playlists/{playlist}/items")
    items = _open(spotify, f"{API}/playlists/{playlist}/items", access)["items"]
    assert len(items) == 4
    first = items[0]
    if kind == "local":
        assert first["is_local"] is True
        assert first["item"]["id"] is None
    elif kind == "episode":
        assert first["item"]["type"] == "episode"
    elif kind == "null":
        assert first["item"] is None
    else:
        assert first == items[1]
    assert len(spotify.playlists[playlist].entries) == 3  # the answer only


def test_stored_unplayable_entries_answer_as_markers(spotify: FakeSpotify) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    episode = "spotify:episode:FakeEpisode00000000002"
    entries = [None, "spotify:local:a:b:c:1", episode, f"spotify:track:{track_id(4)}"]
    playlist = spotify.add_playlist(USER_A, "Mixed", entries).id
    items = _open(spotify, f"{API}/playlists/{playlist}/items", access)["items"]
    assert items[0]["item"] is None
    assert (items[1]["is_local"], items[1]["item"]["id"]) == (True, None)
    assert items[2]["item"]["type"] == "episode"
    assert items[3]["item"]["id"] == track_id(4)


@pytest.mark.parametrize(("method", "status"), [("PUT", None), ("POST", None), ("PUT", 500)])
def test_partial_write(spotify: FakeSpotify, method: str, status: int | None) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    playlist = spotify.add_playlist(USER_A, "Empty").id
    uris = [f"spotify:track:{track_id(i)}" for i in range(10)]
    spotify.inject("partial", keep=3, status=status)
    url = f"{API}/playlists/{playlist}/items"
    if status is None:
        assert _open(spotify, url, access, method, {"uris": uris})["snapshot_id"]
    else:
        assert _error(spotify, url, access, method, {"uris": uris}).code == status
    assert spotify.playlists[playlist].entries == uris[:3]


@pytest.mark.parametrize("path", ["/v1/playlists/{id}/items", "/v1/me/playlists"])
def test_next_points_at_an_unlisted_host(spotify: FakeSpotify, path: str) -> None:
    access, _ = spotify.grant(CLIENT_A, USER_A)
    playlist = _filled(spotify, 60)
    url_path = path.replace("{id}", playlist)
    spotify.inject("next_unlisted", path=url_path)
    page = _open(spotify, f"https://api.spotify.com{url_path}?limit=50", access)
    assert urlsplit(page["next"]).hostname == UNLISTED_HOST


def test_an_unknown_injection_is_refused(spotify: FakeSpotify) -> None:
    with pytest.raises(ValueError, match="unknown injection"):
        spotify.inject("explode")


# --- what it records ------------------------------------------------------------------------


async def test_it_records_each_requests_authorization_and_client_id() -> None:
    fake = standard()
    a = await _connect(fake, CLIENT_A)
    fake.sign_in(USER_B)
    b = await _connect(fake, CLIENT_B)
    _open(fake, f"{API}/me", a["access_token"])
    _open(fake, f"{API}/me", b["access_token"])
    seen = [
        (s.path, s.client_id, s.authorization, s.token_app, s.token_user)
        for s in fake.requests()
        if s.path != "/authorize"
    ]
    assert seen == [
        ("/api/token", CLIENT_A, None, None, None),
        ("/api/token", CLIENT_B, None, None, None),
        ("/v1/me", None, f"Bearer {a['access_token']}", CLIENT_A, USER_A),
        ("/v1/me", None, f"Bearer {b['access_token']}", CLIENT_B, USER_B),
    ]


def test_the_summary_holds_no_token_code_or_verifier(spotify: FakeSpotify) -> None:
    verifier = _verifier()
    code = _approve(spotify, CLIENT_A, verifier)["code"]
    access, refresh = spotify.grant(CLIENT_A, USER_A)
    _open(spotify, f"{API}/me", access)
    text = json.dumps(spotify.summary())
    for value in (verifier, code, access, refresh, challenge_of(verifier)):
        assert value not in text


def test_the_mowl_view_lists_what_a_playlist_holds_now_in_order(spotify: FakeSpotify) -> None:
    """The fake MOWL (in-process and in the e2e stack) wraps a Spotify playlist and lists, from
    this one view, what it holds now: catalogue tracks only, in order, with a BPM that is a fixed
    function of the id. An episode or a null item is left out; an unknown playlist is None."""
    ids = [track_id(5), track_id(1), track_id(3)]
    held = spotify.add_playlist(
        USER_A,
        "Wrapped",
        [f"spotify:track:{ids[0]}", None, f"spotify:episode:{'e' * 22}", f"spotify:track:{ids[1]}"],
    )
    assert spotify.playlist_track_ids(held.id) == ids[:2]
    assert spotify.playlist_track_ids("NoSuchPlaylist0000000") is None

    spotify.set_durations(240_000)
    entries = spotify.mowl_entries(ids, no_bpm={ids[2]})
    assert [e["SpotifyTrackID"] for e in entries] == ids
    assert [e["Duration"] for e in entries] == [240_000] * 3
    assert [e["BPM"] for e in entries] == [100 + 3 * 5, 100 + 3 * 1, 0]  # none for a no_bpm id
    assert {e["Title"] for e in entries} == {spotify.catalogue[i]["name"] for i in ids}
    assert all(
        set(e) == {"TrackID", "Title", "Artist", "Duration", "BPM", "SpotifyTrackID"}
        for e in entries
    )
