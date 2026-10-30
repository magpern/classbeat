"""Connecting Spotify through the API (ADR 0009 sections 1 to 5; issue SC1, #176) against the
in-process fake Spotify. Never the network.

Criteria covered here: the happy path (start, callback, ``/me``, ciphertext-only storage, a
status without any token or Spotify id); every callback mismatch of section 4 changes nothing and
deletes only the caller's own flow, while an unknown state or another user's or session's state
deletes nothing and that flow still completes (two users concurrently); no Client Secret
accepted, requested, stored or logged, the redirect URI never read from input; the feature flag;
recent auth, CSRF and rate limits on the start."""

import asyncio
import logging
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy.engine import Connection

from authtools import ORIGIN, Env, make_settings, session_cookie
from classbeat.config import Settings
from mfatools import Actor
from spotifycore import SCOPES, challenge_of
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B
from spotifytools import CALLBACK_URL, CONSENT, START, STATUS, Spot


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def snapshot(spot: Spot, ctx: Any) -> Any:
    row = await spot.connection(ctx)
    return None if row is None else tuple(row)


# --- the happy path -----------------------------------------------------------------------------


async def test_happy_path_connects_and_stores_ciphertext_only(
    spot: Spot, spotify_admin: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    actor = await spot.actor("rider@example.com")
    ctx = spot.ctx(actor)
    url = await spot.start(actor)

    # The authorize URL: exactly section 4's parameters, the fixed redirect URI, S256.
    assert url.startswith("https://accounts.spotify.com/authorize?")
    params = query_of(url)
    assert set(params) == {
        "client_id", "response_type", "redirect_uri", "scope", "state",
        "code_challenge_method", "code_challenge", "show_dialog",
    }  # fmt: skip
    assert params["client_id"] == CLIENT_A
    assert params["redirect_uri"] == CALLBACK_URL
    assert params["scope"] == SCOPES == "playlist-read-private playlist-modify-private"
    assert (params["response_type"], params["code_challenge_method"]) == ("code", "S256")
    assert params["show_dialog"] == "true"
    # The flow row holds hashes of the state and session and the verifier of the challenge.
    flow = spotify_admin.exec_driver_sql(
        "SELECT state_hash, session_hash, client_id, redirect_uri, verifier, "
        "confirm_account_change, expires_at - created_at FROM spotify_oauth_flows"
    ).one()
    assert params["state"].encode() not in bytes(flow.state_hash)
    assert (flow.client_id, flow.redirect_uri, flow.confirm_account_change) == (
        CLIENT_A, CALLBACK_URL, False
    )  # fmt: skip
    assert challenge_of(flow.verifier) == params["code_challenge"]
    assert flow[6].total_seconds() == 600

    query = spot.approve(url)
    assert await spot.callback(actor, query) == {"result": "connected"}

    # One token request (the stored client id, redirect URI and verifier, no secret), one /me.
    (token,) = spot.token_requests()
    assert token.client_id == CLIENT_A
    assert not token.client_secret
    me = spot.fake.requests("api.spotify.com", "/v1/me")
    assert [(s.token_app, s.token_user) for s in me] == [(CLIENT_A, USER_A)]
    access = me[0].authorization.removeprefix("Bearer ")  # type: ignore[union-attr]
    refresh = next(iter(spot.fake.refresh))

    # Ciphertext only: no token in any column, as pg_dump would print it.
    row = await spot.connection(ctx)
    assert (row.status, row.last_error, row.generation) == ("connected", None, 1)
    assert row.public == {"client_id": CLIENT_A, "spotify_user_id": USER_A}
    dump = spotify_admin.exec_driver_sql("SELECT connections::text FROM connections").scalar_one()
    for secret in (access, refresh, query["code"], query["state"], flow.verifier):
        assert secret not in dump
        assert secret.encode() not in bytes(row.secret)
        assert secret not in caplog.text
    # The flow is gone once used.
    assert await spot.flows(ctx) == []

    # The status: never a token, a Spotify user id or a display name.
    status = await actor.get(STATUS)
    assert status.status_code == 200
    body = status.json()
    assert body == {
        "status": "connected",
        "client_id": CLIENT_A,
        "last_error": None,
        "last_error_message": None,
        "callback_url": CALLBACK_URL,
        "pool_consent": False,
        "consent_version": None,
        "consent_text": body["consent_text"],
        "song_picking_enabled": True,
        "pool_playlist": None,
        "class_playlists": [],
    }
    for hidden in (access, refresh, USER_A, "Rider One"):
        assert hidden not in status.text
    events = spotify_admin.exec_driver_sql(
        "SELECT kind, detail::text FROM auth_events WHERE starts_with(kind, 'connection_')"
    ).all()
    assert [e.kind for e in events] == ["connection_connected"]
    assert all(s not in events[0].detail for s in (access, query["code"], query["state"]))


async def test_a_client_id_is_trimmed_and_lower_cased_and_anything_else_is_422_without_echo(
    spot: Spot,
) -> None:
    actor = await spot.actor("rider@example.com")
    url = await spot.start(actor, f"  {CLIENT_A.upper()}\n")
    assert query_of(url)["client_id"] == CLIENT_A
    for bad in ("not-a-client-id", CLIENT_A + "0", CLIENT_A[:-1] + "g", "x" * 300):
        response = await actor.post(START, {"client_id": bad})
        assert response.status_code == 422
        assert bad not in response.text


# --- no Client Secret, ever ----------------------------------------------------------------------


async def test_a_posted_client_secret_is_422_and_nothing_is_created(
    spot: Spot, spotify_admin: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    actor = await spot.actor("rider@example.com")
    planted = "s3cr3t-value-never-accepted"
    response = await actor.post(START, {"client_id": CLIENT_A, "client_secret": planted})
    assert response.status_code == 422
    assert planted not in response.text
    assert planted not in caplog.text
    assert spotify_admin.exec_driver_sql("SELECT count(*) FROM spotify_oauth_flows").scalar() == 0
    # And the token requests of a whole connect carry none (the fake records and refuses one).
    await spot.connect(actor)
    assert spot.token_requests()
    assert not any(s.client_secret for s in spot.token_requests())
    columns = spotify_admin.exec_driver_sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name IN ('connections', 'spotify_oauth_flows')"
    ).scalars()
    assert not any("secret" in c and c != "secret" and c != "secret_nonce" for c in columns)


async def test_the_redirect_uri_is_never_read_from_input(spot: Spot) -> None:
    """An extra ``redirect_uri`` in the start body is refused; one on the callback is ignored
    (the token request names the stored, fixed one)."""
    actor = await spot.actor("rider@example.com")
    evil = "https://evil.example/oauth/spotify/callback"
    refused = await actor.post(START, {"client_id": CLIENT_A, "redirect_uri": evil})
    assert refused.status_code == 422
    url = await spot.start(actor)
    query = spot.approve(url)
    assert await spot.callback(actor, {**query, "redirect_uri": evil}) == {"result": "connected"}
    (token,) = spot.token_requests()
    assert token.outcome == "answered"
    assert token.status == 200


# --- feature flag, recent auth, CSRF, rate limits ------------------------------------------------


async def test_every_route_is_404_feature_off_when_song_picking_is_off(
    env: Env, make_actor: Any
) -> None:
    actor = await make_actor(await env.user("rider@example.com"))
    calls = [
        ("GET", STATUS),
        ("POST", START),
        ("DELETE", STATUS),
        ("PUT", CONSENT),
        ("DELETE", CONSENT),
    ]
    for method, path in calls:
        response = await actor.call(
            method, path, {"client_id": CLIENT_A} if method == "POST" else None
        )
        assert (response.status_code, response.json()) == (
            404,
            {"detail": {"reason": "feature_off"}},
        )
    response = await actor.get("/oauth/spotify/callback?code=c&state=s")
    assert (response.status_code, response.json()) == (404, {"detail": {"reason": "feature_off"}})


def test_a_localhost_base_url_is_refused_with_song_picking_on(database_url: str) -> None:
    with pytest.raises(ValueError, match="localhost"):
        make_settings(database_url, public_base_url="http://localhost:8000", song_picking=True)
    make_settings(database_url, public_base_url="http://localhost:8000")  # off: fine
    on = make_settings(database_url, public_base_url="http://127.0.0.1:20176", song_picking=True)
    assert isinstance(on, Settings)
    assert on.spotify_redirect_uri == "http://127.0.0.1:20176/oauth/spotify/callback"


async def test_start_needs_recent_auth_and_csrf(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    no_csrf = await actor.client.post(
        START,
        json={"client_id": CLIENT_A},
        headers={"Origin": ORIGIN, **session_cookie(actor.token)},
    )
    assert no_csrf.status_code == 403
    await spot.env.run("UPDATE sessions SET reauth_at = now() - interval '1 hour'")
    stale = await actor.post(START, {"client_id": CLIENT_A})
    assert (stale.status_code, stale.json()) == (403, {"detail": {"reason": "reauth_required"}})
    assert spot.fake.seen == []


async def test_starts_are_rate_limited_and_a_completed_connection_releases_its_slot(
    spot: Spot,
) -> None:
    actor = await spot.actor("rider@example.com")
    for _ in range(9):
        await spot.start(actor)
    assert await spot.connect(actor) == {"result": "connected"}  # the 10th, released
    await spot.start(actor)  # the 10th slot again
    refused = await actor.post(START, {"client_id": CLIENT_A})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0


async def test_at_most_three_open_flows_per_user(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    urls = [await spot.start(actor) for _ in range(4)]
    assert len(await spot.flows(spot.ctx(actor))) == 3
    # The oldest was deleted: its callback is a mismatch; the newest still completes.
    assert await spot.callback(actor, spot.approve(urls[0])) == {"result": "access_denied"}
    assert spot.token_requests() == []
    assert await spot.callback(actor, spot.approve(urls[3])) == {"result": "connected"}


# --- the callback's mismatches -------------------------------------------------------------------


async def connected_user(spot: Spot) -> tuple[Actor, Any, Any]:
    """A user with a working connection (client A), and its row before the test."""
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor) == {"result": "connected"}
    ctx = spot.ctx(actor)
    return actor, ctx, await snapshot(spot, ctx)


async def test_an_expired_flow_changes_nothing_and_is_deleted(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    spotify_admin.exec_driver_sql(
        "UPDATE spotify_oauth_flows SET created_at = now() - interval '11 minutes', "
        "expires_at = now() - interval '1 minute'"
    )
    requests = len(spot.fake.seen)
    query = spot.approve(url)
    assert await spot.callback(actor, query) == {"result": "access_denied"}
    assert len(spot.fake.seen) == requests + 1  # the authorize visit only, no token request
    assert await snapshot(spot, ctx) == before
    assert await spot.flows(ctx) == []


async def test_a_used_flow_is_a_mismatch_and_a_replay_changes_nothing(spot: Spot) -> None:
    actor, ctx, _ = await connected_user(spot)
    url = await spot.start(actor)
    query = spot.approve(url)
    assert await spot.callback(actor, query) == {"result": "connected"}
    after = await snapshot(spot, ctx)
    tokens = len(spot.token_requests())
    assert await spot.callback(actor, query) == {"result": "access_denied"}
    assert len(spot.token_requests()) == tokens
    assert await snapshot(spot, ctx) == after


async def test_a_used_but_unfinished_flow_is_deleted_by_the_mismatch(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    spotify_admin.exec_driver_sql("UPDATE spotify_oauth_flows SET used_at = now()")
    assert await spot.callback(actor, spot.approve(url)) == {"result": "access_denied"}
    assert await spot.flows(ctx) == []
    assert await snapshot(spot, ctx) == before


async def test_a_tampered_stored_redirect_uri_changes_nothing(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    spotify_admin.exec_driver_sql(
        "UPDATE spotify_oauth_flows SET redirect_uri = 'https://evil.example/oauth/spotify/callback'"
    )
    tokens = len(spot.token_requests())
    assert await spot.callback(actor, spot.approve(url)) == {"result": "access_denied"}
    assert len(spot.token_requests()) == tokens  # nothing sent with another redirect URI
    assert await snapshot(spot, ctx) == before
    assert await spot.flows(ctx) == []


async def test_a_callback_not_at_the_fixed_callback_host_changes_nothing(spot: Spot) -> None:
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    query = spot.approve(url)
    other = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=spot.env.app), base_url="https://other.example"
    )
    spot.clients.append(other)
    response = await other.get(
        "/oauth/spotify/callback", params=query, headers=session_cookie(actor.token)
    )
    assert response.headers["location"] == "/account/spotify?result=access_denied"
    assert await snapshot(spot, ctx) == before
    assert await spot.flows(ctx) == []


async def test_a_client_id_swapped_between_start_and_callback_stores_nothing(spot: Spot) -> None:
    """The authorize URL edited to another app: its code is bound to that app, and the token
    request names the stored Client ID, so Spotify refuses it."""
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    swapped = url.replace(f"client_id={CLIENT_A}", f"client_id={CLIENT_B}")
    query = spot.approve(swapped)
    result = await spot.callback(actor, query)
    assert result["result"] != "connected"
    assert spot.token_requests()[-1].client_id == CLIENT_A
    assert await snapshot(spot, ctx) == before
    assert await spot.flows(ctx) == []


async def test_cancel_on_spotify_deletes_the_own_flow_and_makes_no_call(spot: Spot) -> None:
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    spot.fake.deny()
    query = spot.approve(url)
    assert "error" in query
    assert "code" not in query
    tokens = len(spot.token_requests())
    assert await spot.callback(actor, query) == {"result": "access_denied"}
    assert len(spot.token_requests()) == tokens
    assert await snapshot(spot, ctx) == before
    assert await spot.flows(ctx) == []


async def test_an_app_setup_error_on_the_own_flow_is_app_misconfigured(spot: Spot) -> None:
    actor, ctx, before = await connected_user(spot)
    url = await spot.start(actor)
    state = query_of(url)["state"]
    assert await spot.callback(actor, {"error": "invalid_client", "state": state}) == {
        "result": "app_misconfigured"
    }
    assert await snapshot(spot, ctx) == before
    assert await spot.flows(ctx) == []
    # The same error with a state that is not the caller's says nothing more.
    assert await spot.callback(actor, {"error": "invalid_client", "state": "unknown"}) == {
        "result": "access_denied"
    }


async def test_unknown_other_user_and_other_session_states_delete_nothing(spot: Spot) -> None:
    """Another user's or another session's state matches nothing and deletes nothing: that flow
    stays valid and completes afterwards."""
    alice = await spot.actor("alice@example.com")
    bob = await spot.actor("bob@example.com")
    alice_other = await spot.browser(spot.user_id(alice))
    url = await spot.start(alice)
    query = spot.approve(url)
    for wrong in (bob, alice_other):
        assert await spot.callback(wrong, query) == {"result": "access_denied"}
    assert await spot.callback(alice, {"code": query["code"], "state": "unknown"}) == {
        "result": "access_denied"
    }
    anonymous = await spot.env.client.get("/oauth/spotify/callback", params=query)
    assert anonymous.headers["location"] == "/account/spotify?result=access_denied"
    assert spot.token_requests() == []
    assert len(await spot.flows(spot.ctx(alice))) == 1
    assert await spot.callback(alice, query) == {"result": "connected"}
    assert await spot.connection(spot.ctx(bob)) is None


async def test_two_users_concurrently_each_completes_and_a_crossed_callback_does_nothing(
    spot: Spot,
) -> None:
    alice = await spot.actor("alice@example.com")
    bob = await spot.actor("bob@example.com")
    alice_query = spot.approve(await spot.start(alice, CLIENT_A), USER_A)
    bob_query = spot.approve(await spot.start(bob, CLIENT_B), USER_B)
    crossed, alice_done, bob_done, crossed_back = await asyncio.gather(
        spot.callback(bob, alice_query),
        spot.callback(alice, alice_query),
        spot.callback(bob, bob_query),
        spot.callback(alice, bob_query),
    )
    assert (crossed, crossed_back) == ({"result": "access_denied"}, {"result": "access_denied"})
    assert alice_done == bob_done == {"result": "connected"}
    a, b = await spot.connection(spot.ctx(alice)), await spot.connection(spot.ctx(bob))
    assert a.public == {"client_id": CLIENT_A, "spotify_user_id": USER_A}
    assert b.public == {"client_id": CLIENT_B, "spotify_user_id": USER_B}
    # Every request carried its own user's app and token.
    for seen in spot.fake.requests("api.spotify.com"):
        assert (seen.token_app, seen.token_user) in ((CLIENT_A, USER_A), (CLIENT_B, USER_B))
    assert sorted(str(s.client_id) for s in spot.token_requests()) == sorted([CLIENT_A, CLIENT_B])


async def test_the_state_is_single_use_under_two_concurrent_callbacks(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    query = spot.approve(await spot.start(actor))
    results = await asyncio.gather(spot.callback(actor, query), spot.callback(actor, query))
    assert sorted(r["result"] for r in results) == ["access_denied", "connected"]
    assert len(spot.token_requests()) == 1
