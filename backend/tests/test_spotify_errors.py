"""Every category of ADR 0009 section 8, produced by the fake Spotify on connect (the callback's
``result``) and on use (``spotify_call``'s ``ConnectionFailure``), with its plain sentence and
``Retry-After``; no category comes from message text (issue SC1, #176). Never the network."""

import io
import urllib.error
from email.message import Message
from typing import Any

import pytest
from mowl import spotify_web
from mowl.errors import MowlError

from classbeat.connections import ConnectionFailure
from classbeat.spotify import CATEGORIES, SENTENCES, sentence, spotify_call
from classbeat.spotify.errors import QUOTA_THRESHOLD_SECONDS, classify_use
from spotifycore import FakeSpotify
from spotifyfake import CLIENT_A, USER_A, USER_B
from spotifytools import STATUS, Spot

ADR_SENTENCES = {
    "invalid_grant": "Spotify no longer accepts the connection. Reconnect Spotify.",
    "app_misconfigured": "Spotify does not recognise this app setup. Check the Client ID and the "
    "redirect URI in your Spotify app.",
    "not_allowlisted": "Spotify refused this account. In your Spotify app's User Management add "
    "this account's name and email (Development Mode allows 5 people), and make sure the app's "
    "owner has Spotify Premium.",
    "forbidden": "Spotify refused this request for your account or playlist.",
    "rate_limited": "Spotify asks to slow down; trying again in about 2 minutes.",
    "quota_exhausted": "Your Spotify app has used up its allowance for now. Try again in about "
    "3 hours.",
    "upstream_unavailable": "Spotify is not answering.",
    "scope_mismatch": "Spotify did not give the access classbeat needs. Connect again and allow "
    "it.",
    "invalid_response": "Spotify did not give the access classbeat needs. Connect again and allow "
    "it.",
    "account_changed": "This is a different Spotify account than before. Confirm to switch "
    "(classbeat forgets the working playlist).",
    "access_denied": "Spotify was not connected.",
}


def test_every_category_has_its_plain_sentence() -> None:
    assert set(SENTENCES) == set(CATEGORIES)
    hints = {"rate_limited": 90, "quota_exhausted": 2 * 3600 + 1}
    for category, expected in ADR_SENTENCES.items():
        assert sentence(category, hints.get(category)) == expected
    assert sentence("rate_limited", 30) == (
        "Spotify asks to slow down; trying again in about 1 minute."
    )
    assert "Spotify" in sentence("not_found")


def _mowl_error(status: int, message: str, retry_after: str | None = None) -> MowlError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    cause = urllib.error.HTTPError(
        "https://api.spotify.com/v1/me", status, "x", headers, io.BytesIO()
    )
    try:
        raise MowlError(message, status=status) from cause
    except MowlError as error:
        return error


def test_no_category_comes_from_message_text() -> None:
    """Misleading text, the status decides; no status and no HTTP cause is ``invalid_response``."""
    assert classify_use(_mowl_error(404, "HTTP 403 invalid_grant quota")).category == "not_found"
    assert classify_use(_mowl_error(403, "HTTP 404 Not Found")).category == "forbidden"
    assert classify_use(MowlError("HTTP 401 invalid_grant 429")).category == "invalid_response"
    limited = classify_use(_mowl_error(429, "slow down", str(QUOTA_THRESHOLD_SECONDS)))
    assert (limited.category, limited.retry_after) == ("rate_limited", QUOTA_THRESHOLD_SECONDS)
    over = classify_use(_mowl_error(429, "slow down", str(QUOTA_THRESHOLD_SECONDS + 1)))
    assert over.category == "quota_exhausted"
    assert classify_use(ValueError("503 upstream")).category == "unknown"


@pytest.mark.parametrize(
    ("status", "code", "category"),
    [
        (400, "invalid_grant", "invalid_grant"),
        (400, "invalid_client", "app_misconfigured"),
        (401, "invalid_client", "app_misconfigured"),
        (400, "invalid_request", "app_misconfigured"),
        # The other app-setup codes (#198 item 17b, ADR 0009 section 6's amendment).
        (400, "invalid_scope", "app_misconfigured"),
        (400, "unauthorized_client", "app_misconfigured"),
        (401, "unauthorized_client", "app_misconfigured"),
        (400, "unsupported_response_type", "app_misconfigured"),
        # No recognised code: not the app's setup, and never a flip (#198 item 2).
        (400, None, "upstream_unavailable"),
        (401, None, "upstream_unavailable"),
        (400, {"status": 400, "message": "invalid_client"}, "upstream_unavailable"),
        (401, "server_error", "upstream_unavailable"),
        (400, "", "upstream_unavailable"),
        (403, None, "invalid_response"),
        (503, "invalid_client", "upstream_unavailable"),
    ],
)
def test_the_token_endpoints_category_comes_only_from_a_recognised_code(
    status: int, code: object, category: str
) -> None:
    from classbeat.connections.repository import SPOTIFY_FLIP_CATEGORIES
    from classbeat.spotify.errors import by_oauth_code

    assert by_oauth_code(status, code, None) == category
    recognised = code in (
        "invalid_grant", "invalid_client", "invalid_request", "invalid_scope",
        "unauthorized_client", "unsupported_response_type",
    )  # fmt: skip
    assert (category in SPOTIFY_FLIP_CATEGORIES) == (recognised and status < 500)


# --- on connect: the callback's result ----------------------------------------------------------


async def _connect_result(spot: Spot, **kw: Any) -> dict[str, str]:
    actor = await spot.actor("rider@example.com")
    return await spot.connect(actor, **kw)


@pytest.mark.parametrize(
    ("injection", "expected"),
    [
        ({"kind": "invalid_grant", "path": "/api/token"}, {"result": "invalid_grant"}),
        ({"kind": "invalid_client", "path": "/api/token"}, {"result": "app_misconfigured"}),
        (
            {"kind": "status", "status": 503, "path": "/api/token"},
            {"result": "upstream_unavailable"},
        ),
        ({"kind": "refuse", "path": "/api/token"}, {"result": "upstream_unavailable"}),
        # A 400 or 401 without an OAuth ``error`` code is no misconfiguration (#198 item 2).
        (
            {"kind": "status", "status": 400, "path": "/api/token"},
            {"result": "upstream_unavailable"},
        ),
        (
            {"kind": "status", "status": 401, "path": "/api/token"},
            {"result": "upstream_unavailable"},
        ),
        ({"kind": "trickle", "delay": 0.2, "path": "/v1/me"}, {"result": "upstream_unavailable"}),
        (
            {"kind": "rate_limited", "retry_after": 90, "path": "/api/token"},
            {"result": "rate_limited", "retry_after": "90"},
        ),
        (
            {"kind": "rate_limited", "retry_after": 7201, "path": "/v1/me"},
            {"result": "quota_exhausted", "retry_after": "7201"},
        ),
        ({"kind": "status", "status": 401, "path": "/v1/me"}, {"result": "invalid_grant"}),
        ({"kind": "status", "status": 500, "path": "/v1/me"}, {"result": "upstream_unavailable"}),
        ({"kind": "noid", "path": "/v1/me"}, {"result": "invalid_response"}),
    ],
)
async def test_connect_categories_from_the_fake(
    spot: Spot, injection: dict[str, Any], expected: dict[str, str]
) -> None:
    from classbeat.spotify.http import SpotifyHttp
    from spotifyfake import spotify_client

    if injection["kind"] == "trickle":  # a short total deadline for the trickling answer
        spot.env.app.state.spotify_http = SpotifyHttp(
            lambda _: spotify_client(spot.fake, total_seconds=0.5)
        )
    spot.fake.inject(**injection)
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor) == expected
    assert await spot.connection(spot.ctx(actor)) is None  # nothing stored on a failure


async def test_connect_not_allowlisted(spot: Spot) -> None:
    """A 403 on the first ``GET /me``: the user is not on the app's allow-list (Development
    Mode), or the app's owner lacks Premium (the fake's own answer, not an injection)."""
    spot.fake.apps[CLIENT_A].allowed.discard(USER_A)
    assert await _connect_result(spot) == {"result": "not_allowlisted"}
    spot.fake.apps[CLIENT_A].allowed.add(USER_A)
    spot.fake.apps[CLIENT_A].owner_premium = False
    actor = await spot.actor("second@example.com")
    assert await spot.connect(actor) == {"result": "not_allowlisted"}


async def test_connect_scope_mismatch_and_no_refresh_token(
    spot: Spot, monkeypatch: pytest.MonkeyPatch
) -> None:
    spot.fake.apps[CLIENT_A].grant_scope = "playlist-read-private"
    assert await _connect_result(spot) == {"result": "scope_mismatch"}
    spot.fake.apps[CLIENT_A].grant_scope = None
    original = FakeSpotify._issue  # pyright: ignore[reportPrivateUsage]

    def without_refresh(self: FakeSpotify, grant: Any, *, with_refresh: bool) -> Any:
        return original(self, grant, with_refresh=False)

    monkeypatch.setattr(FakeSpotify, "_issue", without_refresh)
    actor = await spot.actor("other@example.com")
    assert await spot.connect(actor) == {"result": "invalid_response"}


async def test_connect_account_changed_and_access_denied(spot: Spot) -> None:
    actor = await spot.actor("rider@example.com")
    assert await spot.connect(actor) == {"result": "connected"}
    assert await spot.connect(actor, user=USER_B) == {"result": "account_changed"}
    spot.fake.deny()  # (``approve`` without a user keeps the denial)
    query = spot.approve(await spot.start(actor))
    assert await spot.callback(actor, query) == {"result": "access_denied"}
    state = (await spot.start(actor)).split("state=")[1].split("&")[0]
    assert await spot.callback(actor, {"error": "invalid_request", "state": state}) == {
        "result": "app_misconfigured"
    }


# --- on use: spotify_call's failure -------------------------------------------------------------


async def _use(
    spot: Spot, fn: Any = spotify_web.me, *, expires_in: float = 3000
) -> ConnectionFailure:
    actor = await spot.actor("rider@example.com")
    ctx = spot.ctx(actor)
    await spot.seed(ctx, expires_in=expires_in)
    with pytest.raises(ConnectionFailure) as failed:
        await spotify_call(spot.env.factory, spot.ring, ctx, fn, http=spot.http)
    status = (await actor.get(STATUS)).json()
    # The status shows the category and its sentence, never a provider message.
    if failed.value.category not in ("rate_limited", "quota_exhausted"):
        assert status["last_error_message"] in (None, sentence(failed.value.category))
    return failed.value


@pytest.mark.parametrize(
    ("injection", "category", "retry_after"),
    [
        ({"kind": "status", "status": 403, "message": "invalid_grant 429"}, "forbidden", None),
        ({"kind": "status", "status": 503}, "upstream_unavailable", None),
        ({"kind": "refuse"}, "upstream_unavailable", None),
        ({"kind": "rate_limited", "retry_after": 120}, "rate_limited", 120),
        ({"kind": "rate_limited", "retry_after": 3601}, "quota_exhausted", 3601),
        ({"kind": "status", "status": 401, "times": 2}, "invalid_grant", None),
    ],
)
async def test_use_categories_from_the_fake(
    spot: Spot, injection: dict[str, Any], category: str, retry_after: int | None
) -> None:
    spot.fake.inject(path="/v1/me", **injection)
    failure = await _use(spot)
    assert (failure.category, failure.retry_after) == (category, retry_after)


async def test_use_not_found_and_the_token_endpoints_categories(spot: Spot) -> None:
    assert (await _use(spot, lambda c: spotify_web.get_playlist(c, "0" * 22))).category == (
        "not_found"
    )
    spot.fake.inject("invalid_client", path="/api/token")
    actor = await spot.actor("second@example.com")
    ctx = spot.ctx(actor)
    await spot.seed(ctx, expires_in=10)
    with pytest.raises(ConnectionFailure) as failed:
        await spotify_call(spot.env.factory, spot.ring, ctx, spotify_web.me, http=spot.http)
    assert failed.value.category == "app_misconfigured"
    status = (await actor.get(STATUS)).json()
    assert (status["status"], status["last_error"]) == ("needs_reconnect", "app_misconfigured")
    assert status["last_error_message"] == ADR_SENTENCES["app_misconfigured"]
