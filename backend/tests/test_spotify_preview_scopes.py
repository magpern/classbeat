"""The opt-in listening-preview scopes and their re-consent (ADR 0042 section 7.1, #1393) through
the API, against the in-process fake Spotify. Never the network.

Covered: without the opt-in nothing new is asked for and the connection is as before (the fake
grants what is asked); the opt-in asks for the two playback scopes, stores the grant and the
status reports ``preview_granted``; a re-consent from a connected row keeps the row, the account
and the pool consent; a refused or narrower consent leaves the connection unchanged or without
preview; a reconnect without the opt-in drops it; a token refresh keeps it; a row from before
migration 0070 (``granted_scopes`` NULL) reports ``preview_granted`` false and still works; no
Client Secret is sent and no token reaches the browser."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from mowl import spotify_web
from sqlalchemy.engine import Connection

from classbeat import connections
from classbeat.spotify import spotify_call
from classbeat.spotify.access import refresh_if_needed
from spotifycore import SCOPES
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B
from spotifytools import CONSENT, START, STATUS, Spot

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

PLAYBACK = ("user-modify-playback-state", "user-read-playback-state")
BASE_KEPT = "playlist-modify-private playlist-modify-public playlist-read-private"
PREVIEW_KEPT = f"{BASE_KEPT} user-modify-playback-state user-read-playback-state"


def asked(url: str) -> list[str]:
    return parse_qs(urlsplit(url).query)["scope"][0].split()


async def row(spot: Spot, ctx: Any) -> Any:
    rows = await spot.owned(
        ctx,
        "SELECT id, status, last_error, public, secret, wrapped_dek, generation, key_id, "
        "granted_scopes FROM connections WHERE provider = 'spotify'",
    )
    return rows[0] if rows else None


async def status(actor: Any) -> dict[str, Any]:
    response = await actor.get(STATUS)
    assert response.status_code == 200, response.text
    return response.json()


async def test_without_the_opt_in_no_new_scope_is_asked_and_the_connection_is_as_before(
    spot: Spot,
) -> None:
    actor = await spot.actor("rider@example.com")
    url = await spot.start(actor)
    assert asked(url) == SCOPES.split()
    assert not set(PLAYBACK) & set(asked(url))
    # ``preview: false`` sent explicitly is the same.
    explicit = await actor.post(START, {"client_id": CLIENT_A, "preview": False})
    assert explicit.status_code == 200
    assert asked(explicit.json()["authorize_url"]) == SCOPES.split()
    assert await spot.callback(actor, spot.approve(url)) == {"result": "connected"}
    found = await row(spot, spot.ctx(actor))
    assert (found.status, found.granted_scopes) == ("connected", BASE_KEPT)
    body = await status(actor)
    assert (body["status"], body["preview_granted"]) == ("connected", False)


async def test_the_opt_in_asks_for_the_playback_scopes_and_reports_them_granted(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor = await spot.actor("rider@example.com")
    url = await spot.start(actor, preview=True)
    assert asked(url) == [*SCOPES.split(), *PLAYBACK]
    query = spot.approve(url)
    assert await spot.callback(actor, query) == {"result": "connected"}
    (token,) = spot.token_requests()
    assert not token.client_secret  # PKCE, still no Client Secret
    found = await row(spot, spot.ctx(actor))
    assert (found.status, found.granted_scopes) == ("connected", PREVIEW_KEPT)
    response = await actor.get(STATUS)
    assert response.json()["preview_granted"] is True
    # No token, code or Spotify id reaches the browser.
    access = spot.fake.requests("api.spotify.com", "/v1/me")[0].authorization or ""
    for hidden in (access.removeprefix("Bearer "), *spot.fake.refresh, query["code"], USER_A):
        assert hidden not in response.text
    dump = spotify_admin.exec_driver_sql("SELECT connections::text FROM connections").scalar_one()
    assert access.removeprefix("Bearer ") not in dump


async def test_a_re_consent_from_a_connected_row_keeps_the_row_account_and_pool(
    spot: Spot,
) -> None:
    actor = await spot.actor("rider@example.com")
    ctx = spot.ctx(actor)
    assert await spot.connect(actor) == {"result": "connected"}
    assert (await actor.call("PUT", CONSENT)).status_code == 204
    before = await row(spot, ctx)
    assert (await status(actor))["preview_granted"] is False

    assert await spot.connect(actor, preview=True) == {"result": "connected"}
    after = await row(spot, ctx)
    assert after.id == before.id
    assert after.public == before.public == {"client_id": CLIENT_A, "spotify_user_id": USER_A}
    assert after.generation == before.generation + 1  # a user's connect, as any reconnect
    assert after.granted_scopes == PREVIEW_KEPT
    body = await status(actor)
    assert (body["preview_granted"], body["pool_consent"]) == (True, True)


async def test_a_refused_consent_leaves_the_connection_unchanged(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    ctx = spot.ctx(actor)
    assert await spot.connect(actor) == {"result": "connected"}
    before = tuple(await row(spot, ctx))
    tokens = len(spot.token_requests())

    url = await spot.start(actor, preview=True)
    spot.fake.deny()  # the user presses Cancel on Spotify's consent page
    query = spot.approve(url)
    assert query["error"] == "access_denied"
    assert await spot.callback(actor, query) == {"result": "access_denied"}
    assert tuple(await row(spot, ctx)) == before
    assert len(spot.token_requests()) == tokens  # no exchange was attempted
    assert await spot.flows(ctx) == []
    body = await status(actor)
    assert (body["status"], body["preview_granted"]) == ("connected", False)


async def test_a_grant_without_the_playback_scopes_connects_without_preview(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    spot.fake.apps[CLIENT_A].grant_scope = SCOPES  # Spotify grants less than was asked
    assert await spot.connect(actor, preview=True) == {"result": "connected"}
    found = await row(spot, spot.ctx(actor))
    assert (found.status, found.granted_scopes) == ("connected", BASE_KEPT)
    assert (await status(actor))["preview_granted"] is False


async def test_a_reconnect_without_the_opt_in_drops_the_preview_grant(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor, preview=True) == {"result": "connected"}
    assert (await status(actor))["preview_granted"] is True
    assert await spot.connect(actor) == {"result": "connected"}
    assert (await row(spot, spot.ctx(actor))).granted_scopes == BASE_KEPT
    assert (await status(actor))["preview_granted"] is False


async def test_an_account_change_stores_the_new_accounts_grant(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor, preview=True) == {"result": "connected"}
    assert await spot.connect(actor, CLIENT_B, USER_B, confirm=True) == {"result": "connected"}
    found = await row(spot, spot.ctx(actor))
    assert (found.public["spotify_user_id"], found.granted_scopes) == (USER_B, BASE_KEPT)
    assert (await status(actor))["preview_granted"] is False


async def test_a_token_refresh_keeps_the_stored_grant(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    ctx = spot.ctx(actor)
    assert await spot.connect(actor, preview=True) == {"result": "connected"}
    opened = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    stale = opened.credentials.spotify_access_token
    assert stale is not None
    tokens = len(spot.token_requests())
    assert await refresh_if_needed(
        spot.env.factory, spot.ring, ctx, spot.http, stale_access_token=stale
    )
    assert len(spot.token_requests()) == tokens + 1  # the refresh was made
    refreshed = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    assert refreshed.credentials.spotify_access_token != stale
    assert (await row(spot, ctx)).granted_scopes == PREVIEW_KEPT
    assert (await status(actor))["preview_granted"] is True


async def test_a_row_from_before_the_migration_reports_no_preview_and_still_works(
    spot: Spot,
) -> None:
    """``spot.seed`` saves as the code before #1393 did: no ``granted_scopes`` (NULL)."""
    actor = await spot.actor("rider@example.com")
    ctx = spot.ctx(actor)
    await spot.seed(ctx)
    assert (await row(spot, ctx)).granted_scopes is None
    body = await status(actor)
    assert (body["status"], body["preview_granted"]) == ("connected", False)
    assert await spotify_call(spot.env.factory, spot.ring, ctx, spotify_web.me, http=spot.http) == {
        "id": USER_A
    }


async def test_preview_is_not_reported_for_a_connection_that_needs_reconnecting(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor, preview=True) == {"result": "connected"}
    spotify_admin.exec_driver_sql(
        "UPDATE connections SET status = 'needs_reconnect', last_error = 'invalid_grant'"
    )
    body = await status(actor)
    assert (body["status"], body["preview_granted"]) == ("needs_reconnect", False)


async def test_only_a_spotify_connection_records_granted_scopes(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    with pytest.raises(ValueError, match="only a Spotify connection"):
        await connections.save(
            spot.env.factory,
            spot.ring,
            spot.ctx(actor),
            "mowl",
            {"token": "t"},
            granted_scopes=PREVIEW_KEPT,
        )
