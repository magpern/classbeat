"""The gateway client (``classbeat.swedebeat_gateway.client``) against the fake gateway: what each
call sends, and every row of ADR 0035 section 3's (login) and section 6's (match) status tables,
plus section 5's session check. No password, key or bearer is ever in an error, and no failure
chains anything. Everything runs over an httpx transport (the netguard holds)."""

import json
import logging
import traceback

import httpx
import pytest
from pydantic import SecretStr

from classbeat.egress import OutboundBlocked
from classbeat.egress_gateway import MATCH_SECONDS, swedebeat_gateway_test_client
from classbeat.swedebeat_gateway import client as gateway_client_module
from classbeat.swedebeat_gateway.client import GatewayFailure, login, match, session
from classbeat.swedebeat_gateway.login_policy import LoginAnswer
from classbeat.swedebeat_gateway.match_policy import Item, Track
from gatewaycore import BEARER_REFUSED, UNKNOWN_CLIENT, FakeGateway
from gatewayfake import (
    CLIENT_KEY,
    DEVICE_ID,
    EMAIL,
    EMAIL_B,
    ORIGIN,
    PASSWORD,
    PASSWORD_B,
    gateway_client,
)

KEY = SecretStr(CLIENT_KEY)
WRONG_KEY = SecretStr("not-the-gateway-client-key-0001")
SONGS = [("Levitating", "Dua Lipa"), ("Blinding Lights", "The Weeknd"), ("Nowhere", "Nobody")]


def secrets_of(failure: BaseException, *extra: str) -> list[str]:
    """Which of the test's secrets show up in the failure's text, repr or traceback."""
    text = "".join(traceback.format_exception(failure)) + repr(failure) + str(failure)
    return [s for s in (PASSWORD, CLIENT_KEY, PASSWORD_B, *extra) if s in text]


def assert_clean(failure: BaseException, *extra: str) -> None:
    assert failure.__cause__ is None
    assert failure.__context__ is None
    assert secrets_of(failure, *extra) == []


async def signed_in(fake: FakeGateway, email: str = EMAIL, password: str = PASSWORD) -> str:
    async with gateway_client(fake) as client:
        return (await login(client, KEY, email, password, DEVICE_ID, force=True)).bearer


async def login_fails(
    fake: FakeGateway, *, key: SecretStr = KEY, password: str = PASSWORD
) -> GatewayFailure:
    async with gateway_client(fake) as client:
        with pytest.raises(GatewayFailure) as failed:
            await login(client, key, EMAIL, password, DEVICE_ID, force=True)
    assert_clean(failed.value)
    return failed.value


# --- What is sent ---------------------------------------------------------------------------


async def test_login_sends_exactly_the_body_and_headers_of_the_adr(gateway: FakeGateway) -> None:
    async with gateway_client(gateway) as client:
        answer = await login(client, KEY, EMAIL, PASSWORD, DEVICE_ID, force=True)
    assert isinstance(answer, LoginAnswer)
    assert answer.expires_in == 86400
    assert answer.bearer in gateway.bearers
    [sent] = gateway.requests
    assert (sent.method, sent.path) == ("POST", "/auth/login")
    assert sent.body == {
        "email": EMAIL,
        "password": PASSWORD,
        "device_id": DEVICE_ID,
        "force": True,
    }
    assert set(sent.headers) == {"host", "accept", "content-type", "content-length", "x-api-key"}
    assert sent.headers["x-api-key"] == CLIENT_KEY
    assert sent.headers["accept"] == "application/json"
    assert sent.headers["content-type"] == "application/json"


async def test_force_is_sent_as_given(gateway: FakeGateway) -> None:
    async with gateway_client(gateway) as client:
        await login(client, KEY, EMAIL, PASSWORD, DEVICE_ID, force=False)
    assert gateway.requests[0].body["force"] is False


async def test_the_login_answer_keeps_only_the_bearer_and_its_lifetime(
    gateway: FakeGateway,
) -> None:
    async with gateway_client(gateway) as client:
        answer = await login(client, KEY, EMAIL, PASSWORD, DEVICE_ID, force=True)
    assert answer.bearer not in repr(answer)
    assert set(LoginAnswer.__dataclass_fields__) == {"bearer", "expires_in"}  # no customer_id


async def test_session_sends_the_key_and_the_bearer_and_no_body(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    async with gateway_client(gateway) as client:
        assert await session(client, KEY, bearer) is None
    sent = gateway.requests[-1]
    assert (sent.method, sent.path, sent.body) == ("GET", "/auth/session", None)
    assert set(sent.headers) == {"host", "accept", "x-api-key", "authorization"}
    assert sent.headers["authorization"] == f"Bearer {bearer}"


async def test_match_sends_titles_and_artists_only_in_tracks_mode(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    async with gateway_client(gateway, total_seconds=MATCH_SECONDS) as client:
        await match(client, KEY, bearer, SONGS)
    sent = gateway.requests[-1]
    assert (sent.method, sent.path) == ("POST", "/catalog/match")
    assert sent.body == {
        "tracks": [{"title": t, "artist": a} for t, a in SONGS],
        "include_unsure": False,
    }
    assert "playlist" not in sent.body
    assert set(sent.headers) == {
        "host",
        "accept",
        "content-type",
        "content-length",
        "x-api-key",
        "authorization",
    }
    assert sent.headers["authorization"] == f"Bearer {bearer}"
    assert sent.headers["x-api-key"] == CLIENT_KEY


async def test_two_users_get_two_bearers_and_each_match_carries_its_own(
    gateway: FakeGateway,
) -> None:
    first, second = await signed_in(gateway), await signed_in(gateway, EMAIL_B, PASSWORD_B)
    assert first != second
    async with gateway_client(gateway) as client:
        await match(client, KEY, second, SONGS[:1])
        await match(client, KEY, first, SONGS[:1])
    matches = [r for r in gateway.requests if r.path == "/catalog/match"]
    assert [r.email for r in matches] == [EMAIL_B, EMAIL]
    assert [r.headers["authorization"] for r in matches] == [f"Bearer {second}", f"Bearer {first}"]


# --- Login: ADR 0035 section 3's table -----------------------------------------------------


async def test_login_unknown_client_is_app_key_unavailable(gateway: FakeGateway) -> None:
    assert (await login_fails(gateway, key=WRONG_KEY)).category == "app_key_unavailable"
    gateway.inject("unknown_client", "/auth/login")
    assert (await login_fails(gateway)).category == "app_key_unavailable"
    assert gateway.bearers == {}  # the key check comes before the login


async def test_a_refused_login_is_invalid_credentials(gateway: FakeGateway) -> None:
    assert (
        await login_fails(gateway, password="not-the-password")
    ).category == "invalid_credentials"


@pytest.mark.parametrize(
    "body",
    [
        {"detail": "Unknown client"},  # the comparison is exact
        {"detail": "unknown client "},
        {"detail": ["unknown client"]},  # a list detail is never compared
        {"detail": [{"type": "x", "loc": ["header"], "msg": "unknown client"}]},
        {"detail": None},
        {"message": UNKNOWN_CLIENT},
        UNKNOWN_CLIENT,
    ],
)
async def test_only_the_exact_string_detail_is_unknown_client(
    gateway: FakeGateway, body: object
) -> None:
    gateway.inject("raw", "/auth/login", status=401, body=json.dumps(body).encode())
    assert (await login_fails(gateway)).category == "invalid_credentials"


async def test_a_non_json_401_is_invalid_credentials(gateway: FakeGateway) -> None:
    gateway.inject("raw", "/auth/login", status=401, body=b"<html>unknown client</html>")
    assert (await login_fails(gateway)).category == "invalid_credentials"


async def test_login_403_is_a_configuration_error(gateway: FakeGateway) -> None:
    gateway.inject("status", "/auth/login", status=403)
    assert (await login_fails(gateway)).category == "app_key_unavailable"


async def test_login_422_is_invalid_credentials(gateway: FakeGateway) -> None:
    gateway.inject("status", "/auth/login", status=422)
    assert (await login_fails(gateway)).category == "invalid_credentials"


@pytest.mark.parametrize("status", [413, 400, 404, 405, 409, 418, 451, 201, 204, 304, 100])
async def test_login_413_or_another_status_is_unknown(gateway: FakeGateway, status: int) -> None:
    gateway.inject("raw", "/auth/login", status=status, body=b"{}")
    assert (await login_fails(gateway)).category == "unknown"


async def test_login_429_is_rate_limited_with_retry_after(gateway: FakeGateway) -> None:
    gateway.inject("rate_limited", "/auth/login", retry_after="42")
    failure = await login_fails(gateway)
    assert (failure.category, failure.retry_after) == ("rate_limited", 42)
    gateway.inject("rate_limited", "/auth/login", retry_after="Wed, 21 Oct 2026 07:28:00 GMT")
    assert (await login_fails(gateway)).retry_after is None  # a date is no hint


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
async def test_login_5xx_is_upstream_unavailable(gateway: FakeGateway, status: int) -> None:
    gateway.inject("status", "/auth/login", status=status)
    failure = await login_fails(gateway)
    assert (failure.category, failure.retry_after) == ("upstream_unavailable", None)


async def test_a_login_timeout_is_upstream_unavailable(gateway: FakeGateway) -> None:
    gateway.inject("slow", "/auth/login", seconds=5)
    async with gateway_client(gateway, total_seconds=1) as client:
        with pytest.raises(GatewayFailure) as failed:
            await login(client, KEY, EMAIL, PASSWORD, DEVICE_ID, force=True)
    assert failed.value.category == "upstream_unavailable"
    assert_clean(failed.value)


async def test_a_refused_connection_is_upstream_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with swedebeat_gateway_test_client(ORIGIN, httpx.MockTransport(refuse)) as client:
        with pytest.raises(GatewayFailure) as failed:
            await login(client, KEY, EMAIL, PASSWORD, DEVICE_ID, force=True)
        assert failed.value.category == "upstream_unavailable"
        assert_clean(failed.value)
        with pytest.raises(GatewayFailure) as failed:
            await match(client, KEY, "b" * 40, SONGS)
        assert failed.value.category == "upstream_unavailable"
        assert_clean(failed.value, "b" * 40)


@pytest.mark.parametrize(
    "injection",
    [
        {"kind": "body", "body": b"not json"},
        {"kind": "body", "body": b"[]"},
        {"kind": "body", "body": b'{"token_type": "Bearer", "expires_in": 86400}'},
        {"kind": "body", "body": b'{"bearer": "short", "expires_in": 86400}'},
        {"kind": "body", "body": json.dumps({"bearer": "x" * 4097, "expires_in": 86400}).encode()},
        {"kind": "body", "body": b'{"bearer": "gAAAAB with a space inside", "expires_in": 86400}'},
        {"kind": "body", "body": b'{"bearer": "gAAAABgAAAABgAAAAB", "expires_in": "86400"}'},
        {"kind": "body", "body": b'{"bearer": "gAAAABgAAAABgAAAAB", "expires_in": true}'},
        {"kind": "body", "body": b'{"bearer": "gAAAABgAAAABgAAAAB", "expires_in": 59}'},
        {"kind": "body", "body": b'{"bearer": "gAAAABgAAAABgAAAAB", "expires_in": 34560001}'},
        {"kind": "body", "body": b'{"bearer": "gAAAABgAAAABgAAAAB", "expires_in": 86400.0}'},
        {"kind": "oversize", "size": 16 * 1024},
        {"kind": "gzip"},
        {"kind": "redirect", "status": 302, "location": "/auth/login"},  # same origin
        {"kind": "redirect", "status": 307, "location": "http://evil.example/auth/login"},
    ],
)
async def test_a_bad_login_body_or_a_redirect_is_unknown(
    gateway: FakeGateway, injection: dict[str, object]
) -> None:
    gateway.inject(path="/auth/login", **injection)  # pyright: ignore[reportArgumentType]
    assert (await login_fails(gateway)).category == "unknown"


async def test_a_login_answer_within_the_bounds_is_kept(gateway: FakeGateway) -> None:
    body = {"bearer": "!" + "~" * 4095, "expires_in": 34560000, "token_type": "x", "extra": [1]}
    gateway.inject("body", "/auth/login", body=json.dumps(body).encode())
    async with gateway_client(gateway) as client:
        answer = await login(client, KEY, EMAIL, PASSWORD, DEVICE_ID, force=True)
    assert (answer.bearer, answer.expires_in) == (body["bearer"], 34560000)


@pytest.mark.parametrize(
    ("email", "password", "device_id"),
    [
        ("ab", PASSWORD, DEVICE_ID),
        ("a" * 255, PASSWORD, DEVICE_ID),
        (EMAIL, "", DEVICE_ID),
        (EMAIL, "p" * 257, DEVICE_ID),
        (EMAIL, PASSWORD, ""),
        (EMAIL, PASSWORD, "d" * 65),
        (EMAIL, PASSWORD, "has space"),
    ],
)
async def test_a_login_outside_the_gateways_bounds_is_refused_before_sending(
    gateway: FakeGateway, email: str, password: str, device_id: str
) -> None:
    async with gateway_client(gateway) as client:
        with pytest.raises(ValueError, match="characters") as refused:
            await login(client, KEY, email, password, device_id, force=True)
    assert gateway.requests == []
    for value in (password, device_id):
        if len(value) > 2:
            assert value not in str(refused.value)


# --- Session: ADR 0035 section 5 -----------------------------------------------------------


async def session_fails(fake: FakeGateway, bearer: str, key: SecretStr = KEY) -> GatewayFailure:
    async with gateway_client(fake) as client:
        with pytest.raises(GatewayFailure) as failed:
            await session(client, key, bearer)
    assert_clean(failed.value, bearer)
    return failed.value


async def test_session_rows(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    assert (await session_fails(gateway, bearer, WRONG_KEY)).category == "app_misconfigured"
    gateway.inject("status", "/auth/session", status=403)
    assert (await session_fails(gateway, bearer)).category == "app_misconfigured"
    gateway.inject("status", "/auth/session", status=502)
    assert (await session_fails(gateway, bearer)).category == "upstream_unavailable"
    gateway.inject("rate_limited", "/auth/session", retry_after="7")
    assert (await session_fails(gateway, bearer)).retry_after == 7
    gateway.inject("body", "/auth/session", body=b'{"valid": false}')
    assert (await session_fails(gateway, bearer)).category == "invalid_response"
    gateway.expire(bearer)
    assert (await session_fails(gateway, bearer)).category == "bearer_refused"
    assert (await session_fails(gateway, "g" * 40)).category == "bearer_refused"


async def test_session_after_the_ttl_is_bearer_refused(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    gateway.advance(86400 + 1)
    assert (await session_fails(gateway, bearer)).category == "bearer_refused"


async def test_a_session_redirect_is_outbound_blocked(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("redirect", "/auth/session", status=302, location="/auth/login")
    async with gateway_client(gateway) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await session(client, KEY, bearer)
    assert refused.value.after_request is True
    assert_clean(refused.value, bearer)


# --- Match: ADR 0035 section 6's table -----------------------------------------------------


async def match_fails(
    fake: FakeGateway, bearer: str, *, key: SecretStr = KEY, total_seconds: float = MATCH_SECONDS
) -> GatewayFailure:
    async with gateway_client(fake, total_seconds=total_seconds) as client:
        with pytest.raises(GatewayFailure) as failed:
            await match(client, key, bearer, SONGS)
    assert_clean(failed.value, bearer)
    return failed.value


async def test_match_200_gives_one_kept_item_per_song_in_order(gateway: FakeGateway) -> None:
    gateway.add_track("Levitating", "Dua Lipa", "found")
    gateway.add_track("Blinding Lights", "The Weeknd", "unsure")
    bearer = await signed_in(gateway)
    async with gateway_client(gateway, total_seconds=MATCH_SECONDS) as client:
        items = await match(client, KEY, bearer, SONGS)
    assert items == [
        Item("found", 0.93, Track("Levitating", "Dua Lipa", 240)),
        Item("unsure", 0.61, Track("A Different Tune Entirely", "The Weeknd", 240)),
        Item("missing", 0.0, None),
    ]


async def test_all_missing_comes_back_as_it_is(gateway: FakeGateway) -> None:
    gateway.add_track("Levitating", "Dua Lipa", "found")
    bearer = await signed_in(gateway)
    gateway.inject("all_missing", "/catalog/match")
    async with gateway_client(gateway) as client:
        items = await match(client, KEY, bearer, SONGS)
    assert [i.status for i in items] == ["missing"] * 3
    assert all(i.track is None for i in items)


async def test_match_unknown_client_is_app_misconfigured(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    assert (await match_fails(gateway, bearer, key=WRONG_KEY)).category == "app_misconfigured"
    gateway.inject("unknown_client", "/catalog/match")
    assert (await match_fails(gateway, bearer)).category == "app_misconfigured"


async def test_any_other_match_401_is_bearer_refused(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("bearer_401", "/catalog/match")
    assert (await match_fails(gateway, bearer)).category == "bearer_refused"
    assert (
        await match_fails(gateway, "not-a-bearer-the-fake-issued-0001")
    ).category == "bearer_refused"
    gateway.inject("raw", "/catalog/match", status=401, body=b'{"detail": ["unknown client"]}')
    assert (await match_fails(gateway, bearer)).category == "bearer_refused"
    gateway.expire(bearer)
    assert (await match_fails(gateway, bearer)).category == "bearer_refused"


async def test_match_403_is_app_misconfigured(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("status", "/catalog/match", status=403)
    assert (await match_fails(gateway, bearer)).category == "app_misconfigured"


async def test_match_429_is_rate_limited_with_retry_after(gateway: FakeGateway) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("rate_limited", "/catalog/match", retry_after="120")
    failure = await match_fails(gateway, bearer)
    assert (failure.category, failure.retry_after) == ("rate_limited", 120)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_match_5xx_is_upstream_unavailable(gateway: FakeGateway, status: int) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("status", "/catalog/match", status=status)
    assert (await match_fails(gateway, bearer)).category == "upstream_unavailable"


async def test_a_match_timeout_or_the_deadline_is_upstream_unavailable(
    gateway: FakeGateway,
) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("slow", "/catalog/match", seconds=5)
    assert (await match_fails(gateway, bearer, total_seconds=1)).category == "upstream_unavailable"
    gateway.inject("trickle", "/catalog/match", seconds=0.05)
    assert (await match_fails(gateway, bearer, total_seconds=1)).category == "upstream_unavailable"


@pytest.mark.parametrize(
    "injection",
    [
        {"kind": "status", "status": 413},
        {"kind": "status", "status": 422},
        {"kind": "status", "status": 400},
        {"kind": "status", "status": 404},
        {"kind": "raw", "status": 201, "body": b"{}"},
        {"kind": "body", "body": b"not json"},
        {"kind": "body", "body": b'{"items": "nope"}'},
        {"kind": "body", "body": b"[]"},
        {"kind": "body", "body": json.dumps({"items": [{"status": "found"}] * 2}).encode()},
        {"kind": "body", "body": json.dumps({"items": [{"status": "maybe"}] * 3}).encode()},
        {"kind": "body", "body": json.dumps({"items": ["found"] * 3}).encode()},
        {"kind": "wrong_count"},
        {"kind": "oversize", "size": 1024 * 1024},
        {"kind": "gzip"},
    ],
)
async def test_413_422_another_status_or_a_bad_body_is_invalid_response(
    gateway: FakeGateway, injection: dict[str, object]
) -> None:
    bearer = await signed_in(gateway)
    gateway.inject(path="/catalog/match", **injection)  # pyright: ignore[reportArgumentType]
    assert (await match_fails(gateway, bearer)).category == "invalid_response"


@pytest.mark.parametrize(
    ("status", "location"),
    [(302, "/catalog/match"), (307, "/auth/login"), (301, "http://evil.example/"), (308, "x")],
)
async def test_a_match_redirect_is_outbound_blocked(
    gateway: FakeGateway, status: int, location: str
) -> None:
    bearer = await signed_in(gateway)
    gateway.inject("redirect", "/catalog/match", status=status, location=location)
    async with gateway_client(gateway) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await match(client, KEY, bearer, SONGS)
    assert refused.value.after_request is True
    assert_clean(refused.value, bearer)
    assert [r.path for r in gateway.requests].count("/catalog/match") == 1  # never followed


@pytest.mark.parametrize(
    "tracks",
    [
        [],
        [("", "Artist")],
        [("Title", "  ")],
        [("t" * 201, "a")],
        [("t", "a" * 201)],
        [("t", "a")] * 201,
    ],
)
async def test_a_match_outside_the_bounds_is_refused_before_sending(
    gateway: FakeGateway, tracks: list[tuple[str, str]]
) -> None:
    bearer = await signed_in(gateway)
    async with gateway_client(gateway) as client:
        with pytest.raises(ValueError, match=r"match sends|sent title"):
            await match(client, KEY, bearer, tracks)
    assert [r.path for r in gateway.requests] == ["/auth/login"]


@pytest.mark.parametrize(
    "bearer", ["short", "b" * 4097, "gAAAAB with space 0123", "gAAAAB\r\nX-Evil: 1-0123"]
)
async def test_a_malformed_bearer_is_refused_before_sending(
    gateway: FakeGateway, bearer: str
) -> None:
    async with gateway_client(gateway) as client:
        with pytest.raises(ValueError, match="the bearer") as refused:
            await match(client, KEY, bearer, SONGS)
        with pytest.raises(ValueError, match="the bearer"):
            await session(client, KEY, bearer)
    assert bearer not in str(refused.value)
    assert gateway.requests == []


# --- Secrecy and shape ---------------------------------------------------------------------


async def test_nothing_secret_is_logged(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        bearer = await signed_in(gateway)
        await login_fails(gateway, password="wrong-password-0001")
        gateway.inject("redirect", "/catalog/match", status=302, location="http://evil.example/")
        async with gateway_client(gateway) as client:
            with pytest.raises(OutboundBlocked):
                await match(client, KEY, bearer, SONGS)
    for secret in (PASSWORD, CLIENT_KEY, bearer, "wrong-password-0001"):
        assert secret not in caplog.text


def test_the_module_exposes_exactly_login_session_and_match() -> None:
    functions = {
        name
        for name, value in vars(gateway_client_module).items()
        if callable(value)
        and not name.startswith("_")
        and not isinstance(value, type)
        and getattr(value, "__module__", "") == gateway_client_module.__name__
    }
    assert functions == {"login", "session", "match"}
    assert set(gateway_client_module.__all__) == {
        "GatewayFailure",
        "Item",
        "LoginAnswer",
        "login",
        "match",
        "session",
    }


def test_the_fake_keeps_the_contracts_order_of_checks() -> None:
    fake = FakeGateway({CLIENT_KEY})
    big = b"x" * (2 * 1024 * 1024 + 1)
    # 413 first, before the key is looked at.
    assert fake.handle("POST", "/catalog/match", {}, big).status == 413
    # Then the key, before the bearer and the schema.
    reply = fake.handle("POST", "/catalog/match", {"x-api-key": "wrong"}, b"not json")
    assert (reply.status, json.loads(reply.body)) == (401, {"detail": UNKNOWN_CLIENT})
    # Then the bearer, before the schema.
    reply = fake.handle("POST", "/catalog/match", {"x-api-key": CLIENT_KEY}, b"not json")
    assert (reply.status, json.loads(reply.body)) == (401, {"detail": BEARER_REFUSED})
    # The login needs no bearer; its schema error is a list detail.
    reply = fake.handle("POST", "/auth/login", {"x-api-key": CLIENT_KEY}, b'{"email": 1}')
    assert reply.status == 422
    assert isinstance(json.loads(reply.body)["detail"], list)
    assert fake.handle("GET", "/health", {}, b"").status == 200
