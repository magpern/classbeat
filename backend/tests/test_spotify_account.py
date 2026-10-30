"""A changed Client ID, another Spotify account, Disconnect and the pool consent through the API
(ADR 0009 sections 5 step 2, 7 and 9; issue SC1, #176). Each does exactly what the ADR says to
the tokens, the pool, the Spotify operations and the class playlists, nothing else (MOWL links
are never touched), and sends no Spotify request of its own. Never the network."""

from typing import Any

from mowl import spotify_web
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import ORIGIN, session_cookie
from classbeat.db import user_session
from classbeat.spotify import spotify_call
from jobtools import new_class
from mfatools import Actor
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B
from spotifytools import CONSENT, STATUS, Spot
from test_spotify_forget import POOL_PLAYLIST, expect_pool_forgotten, operation, plant, state


async def connected(spot: Spot, admin: Connection) -> tuple[Actor, Any, dict[str, Any]]:
    """A user connected with app A as Spotify user A, and every Spotify-derived row planted."""
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor) == {"result": "connected"}
    return actor, spot.ctx(actor), plant(admin, spot.user_id(actor))


async def row(spot: Spot, ctx: Any) -> Any:
    found = await spot.connection(ctx)
    return None if found is None else tuple(found)


async def test_a_client_id_change_keeps_the_old_tokens_until_the_new_flow_succeeds(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, planted = await connected(spot, spotify_admin)
    before, connection_before = state(spotify_admin, spot.user_id(actor)), await row(spot, ctx)
    url = await spot.start(actor, CLIENT_B)
    # Between start and callback nothing changed and the old tokens are in use.
    assert await row(spot, ctx) == connection_before
    assert state(spotify_admin, spot.user_id(actor)) == before
    assert await spotify_call(
        spot.env.factory, spot.ring, ctx, spotify_web.me, http=spot.http
    ) == {"id": USER_A}  # fmt: skip
    assert spot.fake.requests("api.spotify.com", "/v1/me")[-1].token_app == CLIENT_A
    status = (await actor.get(STATUS)).json()
    assert (status["status"], status["client_id"]) == ("connected", CLIENT_A)

    seen = len(spot.fake.seen)
    query = spot.approve(url, USER_A)
    assert await spot.callback(actor, query) == {"result": "connected"}
    # Only the authorize visit, the token request and the GET /me of the new flow.
    assert [(s.host, s.path) for s in spot.fake.seen[seen:]] == [
        ("accounts.spotify.com", "/authorize"),
        ("accounts.spotify.com", "/api/token"),
        ("api.spotify.com", "/v1/me"),
    ]
    after = await spot.connection(ctx)
    assert after.public == {"client_id": CLIENT_B, "spotify_user_id": USER_A}
    assert after.secret != connection_before[4]
    # Tokens replaced, generation + 1, the pool forgotten (consent asked again), class playlists
    # and everything else kept.
    assert state(spotify_admin, spot.user_id(actor)) == {
        **expect_pool_forgotten(before, planted), "pool": [], "connection": {2},
    }  # fmt: skip
    status = (await actor.get(STATUS)).json()
    assert (status["client_id"], status["pool_consent"]) == (CLIENT_B, False)


async def test_a_failed_or_abandoned_client_id_change_leaves_the_connection_untouched(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, _ = await connected(spot, spotify_admin)
    before, connection_before = state(spotify_admin, spot.user_id(actor)), await row(spot, ctx)
    await spot.start(actor, CLIENT_B)  # abandoned
    spot.fake.deny()
    query = spot.approve(await spot.start(actor, CLIENT_B))
    assert await spot.callback(actor, query) == {"result": "access_denied"}
    spot.fake.sign_in(USER_A)
    spot.fake.inject("invalid_client", path="/api/token")
    query = spot.approve(await spot.start(actor, CLIENT_B))
    assert await spot.callback(actor, query) == {"result": "app_misconfigured"}
    assert await row(spot, ctx) == connection_before
    assert state(spotify_admin, spot.user_id(actor)) == before


async def test_a_reconnect_keeps_the_pool_and_everything(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, _ = await connected(spot, spotify_admin)
    before, connection_before = state(spotify_admin, spot.user_id(actor)), await row(spot, ctx)
    await spot.env.run("UPDATE sessions SET reauth_at = now()")
    assert await spot.connect(actor) == {"result": "connected"}
    assert state(spotify_admin, spot.user_id(actor)) == {**before, "connection": {2}}
    after = await spot.connection(ctx)
    assert after.secret != connection_before[4]
    assert after.public == {"client_id": CLIENT_A, "spotify_user_id": USER_A}


async def test_another_spotify_account_without_confirmation_stores_nothing(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, _ = await connected(spot, spotify_admin)
    before, connection_before = state(spotify_admin, spot.user_id(actor)), await row(spot, ctx)
    assert await spot.connect(actor, CLIENT_A, USER_B) == {"result": "account_changed"}
    assert await row(spot, ctx) == connection_before
    assert state(spotify_admin, spot.user_id(actor)) == before
    assert await spot.flows(ctx) == []


async def test_another_spotify_account_with_confirmation_forgets_the_old_one(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, ctx, planted = await connected(spot, spotify_admin)
    before = state(spotify_admin, spot.user_id(actor))
    assert await spot.connect(actor, CLIENT_A, USER_B, confirm=True) == {"result": "connected"}
    assert state(spotify_admin, spot.user_id(actor)) == {
        **before,
        "operations": {planted["attach"]},  # every Spotify operation deleted, MOWL's kept
        "pool": [],
        "live": set(),
        "detached": {planted["playlist"]},
        "connection": {2},
    }
    assert (await spot.connection(ctx)).public == {"client_id": CLIENT_A, "spotify_user_id": USER_B}


async def test_disconnect_forgets_the_account_and_makes_no_spotify_request(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor, _ctx, planted = await connected(spot, spotify_admin)
    other = await spot.actor("other@example.com")
    assert await spot.connect(other, CLIENT_B, USER_B) == {"result": "connected"}
    plant(spotify_admin, spot.user_id(other))
    other_before = state(spotify_admin, spot.user_id(other))
    before = state(spotify_admin, spot.user_id(actor))
    seen = len(spot.fake.seen)

    no_csrf = await actor.client.delete(
        STATUS, headers={"Origin": ORIGIN, **session_cookie(actor.token)}
    )
    assert no_csrf.status_code == 403
    await spot.env.run(
        "UPDATE sessions SET reauth_at = now() - interval '1 hour' WHERE user_id = :u",
        u=spot.user_id(actor),
    )
    stale = await actor.call("DELETE", STATUS)
    assert (stale.status_code, stale.json()) == (403, {"detail": {"reason": "reauth_required"}})
    assert state(spotify_admin, spot.user_id(actor)) == before

    await spot.env.run(
        "UPDATE sessions SET reauth_at = now() WHERE user_id = :u", u=spot.user_id(actor)
    )
    assert (await actor.call("DELETE", STATUS)).status_code == 204
    assert state(spotify_admin, spot.user_id(actor)) == {
        **before,
        "operations": {planted["attach"]},
        "pool": [],
        "live": set(),
        "detached": {planted["playlist"]},
        "connection": set(),
    }
    assert len(spot.fake.seen) == seen  # locally only: nothing sent to Spotify
    assert state(spotify_admin, spot.user_id(other)) == other_before
    assert (await actor.get(STATUS)).json()["status"] == "not_connected"
    kinds = (
        spotify_admin.exec_driver_sql(
            "SELECT kind FROM auth_events WHERE detail->>'provider' = 'spotify' ORDER BY at"
        )
        .scalars()
        .all()
    )
    assert kinds[-1] == "connection_disconnected"
    assert (await actor.call("DELETE", STATUS)).status_code == 204  # idempotent


async def test_pool_consent_give_revoke_and_a_new_consent_never_reuses_the_old_playlist(
    spot: Spot, spotify_admin: Connection
) -> None:
    actor = await spot.actor("rider@example.com")
    owner = spot.user_id(actor)
    refused = await actor.call("PUT", CONSENT)
    assert (refused.status_code, refused.json()) == (409, {"detail": {"reason": "not_connected"}})
    assert await spot.connect(actor) == {"result": "connected"}
    seen = len(spot.fake.seen)

    assert (await actor.call("PUT", CONSENT)).status_code == 204
    pool = spotify_admin.exec_driver_sql(
        "SELECT spotify_user_id, playlist_id, consent_version FROM spotify_pool"
    ).one()
    assert tuple(pool) == (USER_A, None, 1)
    status = (await actor.get(STATUS)).json()
    assert (status["pool_consent"], status["consent_version"]) == (True, 1)
    assert "classbeat working copy" in status["consent_text"]

    # A pick happened: the pool playlist exists and its operations are recorded.
    spotify_admin.exec_driver_sql(
        f"UPDATE spotify_pool SET playlist_id = '{POOL_PLAYLIST}'"  # noqa: S608 - a constant
    )
    created = operation(spotify_admin, owner, "spotify_create_playlist", state="succeeded",
                        external_id=POOL_PLAYLIST,
                        request_context={"purpose": "pool", "ref": "a1b2c3d4e5f6"})  # fmt: skip
    replaced = operation(spotify_admin, owner, "spotify_replace_items", state="uncertain")
    planted = plant_final(spotify_admin, owner)

    await spot.env.run("UPDATE sessions SET reauth_at = now() - interval '1 hour'")
    stale = await actor.call("DELETE", CONSENT)
    assert (stale.status_code, stale.json()) == (403, {"detail": {"reason": "reauth_required"}})
    await spot.env.run("UPDATE sessions SET reauth_at = now()")

    assert (await actor.call("DELETE", CONSENT)).status_code == 204
    assert spotify_admin.exec_driver_sql("SELECT count(*) FROM spotify_pool").scalar() == 0
    left = set(spotify_admin.exec_driver_sql("SELECT id FROM external_operations").scalars())
    assert created not in left
    assert replaced not in left
    assert left == planted  # the final playlist's operations stay
    assert (await actor.get(STATUS)).json()["pool_consent"] is False

    # A new consent starts from nothing: no row anywhere names the old playlist id.
    assert (await actor.call("PUT", CONSENT)).status_code == 204
    assert spotify_admin.exec_driver_sql("SELECT playlist_id FROM spotify_pool").scalar() is None
    named = spotify_admin.execute(
        text(
            "SELECT count(*) FROM external_operations WHERE external_id = :p "
            "OR strpos(request_context::text, :p) > 0"
        ),
        {"p": POOL_PLAYLIST},
    ).scalar()
    assert named == 0
    assert len(spot.fake.seen) == seen  # consent and revoke send nothing to Spotify
    assert not any(POOL_PLAYLIST in s.path for s in spot.fake.seen)
    kinds = (
        spotify_admin.exec_driver_sql(
            "SELECT kind FROM auth_events WHERE starts_with(kind, 'spotify_pool') ORDER BY at, id"
        )
        .scalars()
        .all()
    )
    assert kinds == ["spotify_pool_consent", "spotify_pool_revoked", "spotify_pool_consent"]


def plant_final(admin: Connection, owner: Any) -> set[Any]:
    class_id = new_class(admin, owner)
    return {
        operation(admin, owner, "spotify_create_playlist", class_id=class_id, state="succeeded",
                  external_id="0vvXsWCC9xrXsKd4FyS8kM"),
        operation(admin, owner, "spotify_replace_items", class_id=class_id),
    }  # fmt: skip


async def test_consent_needs_a_connected_row(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor) == {"result": "connected"}
    await spot.env.run("UPDATE sessions SET reauth_at = now()")
    async with user_session(spot.env.factory, spot.ctx(actor)) as db:
        await db.execute(text("UPDATE connections SET status = 'needs_reconnect'"))
    refused = await actor.call("PUT", CONSENT)
    assert (refused.status_code, refused.json()) == (409, {"detail": {"reason": "not_connected"}})
