"""A2 of ADR 0016: ``GET /api/music/swedebeat-mixes``. Flag, MOWL connection, rate limit, the shape
of the answer, two users with the same mix id, and every failure category, against the in-process
fake MOWL (``mowlfake``, loopback HTTP); no Swedebeat token and no Swedebeat host is involved.
Never the network."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from authtools import Env, running_env
from classbeat import connections
from classbeat.auth import ratelimit
from ctx import make_context
from jobapi import Browser
from mowlfake import FakeMowl
from optools import TOKEN_A, TOKEN_B

pytestmark = [pytest.mark.usefixtures("jenv", "mowl_api")]

URL = "/api/music/swedebeat-mixes"
USER_LIMIT = ratelimit.SWEDEBEAT_MIXES_USER.max_count
SAME_ID = "shared-mix-0001"
LIST = "POST /v1/swedebeat/Playlists"


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, swedebeat_enabled=True) as running:
        yield running


async def rider(browsers: Any, env: Env, token: str | None = TOKEN_A) -> Browser:
    browser: Browser = await browsers(mowl=False)
    if token is not None:
        await connections.save(
            env.factory, env.app.state.keyring, make_context(browser.user_id), "mowl",
            {"token": token},
        )  # fmt: skip
    return browser


def plant_map(env: Env, owner: UUID, mix_id: str, superuser: Any) -> None:
    superuser.execute(
        text(
            "INSERT INTO swedebeat_mix_maps (owner_id, mix_id, mowl_connection_id, playlist_id, "
            "name, duration, listed_duration, map) VALUES (:o, :m, gen_random_uuid(), 9001, "
            "'Planted', 100, 100, '{}'::jsonb)"
        ),
        {"o": owner, "m": mix_id},
    )


def test_the_limits_are_per_user_and_per_ip_and_the_ip_is_wider() -> None:
    assert (USER_LIMIT, ratelimit.SWEDEBEAT_MIXES_USER.scope) == (30, "account")
    assert ratelimit.SWEDEBEAT_MIXES_IP.max_count == 60
    assert ratelimit.SWEDEBEAT_MIXES_USER.window_seconds == 600
    # Its own buckets: the playlist picker's count is not shared.
    assert ratelimit.SWEDEBEAT_MIXES_USER.action != ratelimit.MOWL_PLAYLISTS_USER.action


async def test_the_list_is_the_callers_own_mixes_with_names_and_lengths_only(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env)
    mix = mowl_api.add_mix("Sunday Climb", token=TOKEN_A, listed_seconds=1830)
    mowl_api.add_mix("Ride‮\x00 two", token=TOKEN_A, listed_seconds=60)
    mowl_api.add_mix("Theirs", token=TOKEN_B)
    response = await browser.get(URL)
    assert response.status_code == 200, response.text
    listed = response.json()["mixes"]
    assert [(m["name"], m["duration_seconds"], m["analysed"]) for m in listed] == [
        ("Sunday Climb", 1830, False),
        ("Ride two", 60, False),
    ]
    assert listed[0]["mix_id"] == mix
    assert mowl_api.requests == [LIST]  # one read, MOWL only
    text = response.text
    for forbidden in (TOKEN_A, "PlaylistID", "URL", "mix:", "Theirs"):
        assert forbidden not in text


async def test_an_attached_mix_does_not_leak_its_playlist_id(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env)
    mix = mowl_api.add_mix("Mix", token=TOKEN_A)
    from mowl import api
    from mowl.credentials import Credentials

    playlist = api.attach_swedebeat(Credentials(mowl_token=TOKEN_A), mix)["PlaylistID"]
    response = await browser.get(URL)
    assert str(playlist) not in response.text
    assert set(response.json()["mixes"][0]) == {"mix_id", "name", "duration_seconds", "analysed"}


async def test_the_analysed_badge_is_the_callers_own_rows_only(
    browsers: Any, env: Env, mowl_api: FakeMowl, superuser: Any
) -> None:
    mine = await rider(browsers, env, TOKEN_A)
    other = await rider(browsers, env, TOKEN_B)
    mowl_api.add_mix("Mine", token=TOKEN_A, mix_id=SAME_ID)
    mowl_api.add_mix("Theirs", token=TOKEN_B, mix_id=SAME_ID)
    plant_map(env, other.user_id, SAME_ID, superuser)  # only the other user has analysed it
    superuser.commit()
    assert (await mine.get(URL)).json()["mixes"][0]["analysed"] is False
    assert (await other.get(URL)).json()["mixes"][0]["analysed"] is True
    plant_map(env, mine.user_id, SAME_ID, superuser)
    superuser.commit()
    assert (await mine.get(URL)).json()["mixes"][0]["analysed"] is True


async def test_two_users_with_the_same_mix_id_see_only_their_own_mix(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    mine = await rider(browsers, env, TOKEN_A)
    other = await rider(browsers, env, TOKEN_B)
    mowl_api.add_mix("Alice's mix", token=TOKEN_A, mix_id=SAME_ID)
    mowl_api.add_mix("Bob's mix", token=TOKEN_B, mix_id=SAME_ID)
    assert [m["name"] for m in (await mine.get(URL)).json()["mixes"]] == ["Alice's mix"]
    assert [m["name"] for m in (await other.get(URL)).json()["mixes"]] == ["Bob's mix"]


async def test_no_mix_is_an_empty_list_and_a_malformed_entry_is_left_out(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Only", token=TOKEN_A, mix_id="has space")
    mowl_api.add_mix("Good", token=TOKEN_A, mix_id="good-1")
    mowl_api.add_mix("Url", token=TOKEN_A, mix_id="https://evil.example/x")
    assert [m["mix_id"] for m in (await browser.get(URL)).json()["mixes"]] == ["good-1"]


async def test_a_signed_out_caller_is_refused_and_a_user_without_mowl_gets_409(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env, None)
    assert (await browser.client.get(URL)).status_code == 401
    refused = await browser.get(URL)
    assert (refused.status_code, refused.json()) == (409, {"detail": {"reason": "not_connected"}})
    assert mowl_api.requests == []


# --- the flag -------------------------------------------------------------------------------


async def test_flag_off_is_404_before_any_connection_or_provider_work(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env, None)  # no MOWL connection either
    mowl_api.add_mix("Mix", token=TOKEN_A)
    env.app.state.settings = env.settings.model_copy(update={"swedebeat_enabled": False})
    for _ in range(USER_LIMIT + 5):  # no rate-limit slot is spent either
        refused = await browser.get(URL)
        assert (refused.status_code, refused.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert mowl_api.requests == []
    env.app.state.settings = env.settings.model_copy(update={"swedebeat_enabled": True})
    assert (await browser.get(URL)).status_code == 409  # not rate limited: the flag took no slot


async def test_the_admin_flag_off_is_404_too(
    browsers: Any, env: Env, mowl_api: FakeMowl, superuser: Any
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    superuser.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', CAST(:v AS jsonb))"
        ),
        {"v": json.dumps("off")},
    )
    superuser.commit()
    try:
        assert (await browser.get(URL)).status_code == 404
        assert mowl_api.requests == []
    finally:
        superuser.execute(text("DELETE FROM app_settings"))
        superuser.commit()


# --- failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "status", "reason"),
    [
        (401, 409, "needs_reconnect"),
        (429, 429, "rate_limited"),
        (503, 503, "upstream_unavailable"),
        (500, 503, "upstream_unavailable"),
    ],
)
async def test_each_listing_failure_answers_its_category_only(
    browsers: Any, env: Env, mowl_api: FakeMowl, code: int, status: int, reason: str
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.status["swedebeat_list"] = code
    mowl_api.retry_after = "42"
    response = await browser.get(URL)
    assert (response.status_code, response.json()) == (status, {"detail": {"reason": reason}})
    if code == 429:
        assert response.headers["Retry-After"] == "42"
    assert TOKEN_A not in response.text


async def test_a_get_writes_no_state_even_when_mowl_refuses_the_token(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    mowl_api.status["swedebeat_list"] = 401

    async def row() -> Any:
        [info] = await connections.list_connections(env.factory, make_context(browser.user_id))
        return (info.status, info.last_error)

    before = await row()
    await browser.get(URL)
    assert await row() == before == ("connected", None)


# --- the rate limit ---------------------------------------------------------------------------


async def test_a_request_over_the_limit_is_429_before_any_mowl_call(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    for _ in range(USER_LIMIT):
        assert (await browser.get(URL)).status_code == 200
    refused = await browser.get(URL)
    assert (refused.status_code, refused.json()) == (429, {"detail": {"reason": "rate_limited"}})
    assert 0 < int(refused.headers["Retry-After"]) <= 600
    assert len(mowl_api.requests) == USER_LIMIT


async def test_one_user_over_the_limit_does_not_limit_another(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    mine = await rider(browsers, env, TOKEN_A)
    other = await rider(browsers, env, TOKEN_B)
    mowl_api.add_mix("A", token=TOKEN_A)
    mowl_api.add_mix("B", token=TOKEN_B)
    for _ in range(USER_LIMIT):
        await mine.get(URL)
    assert (await mine.get(URL)).status_code == 429
    assert (await other.get(URL)).status_code == 200
