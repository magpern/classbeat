"""Issue #525: the per-user "I use Swedebeat" preference, default off (ADR 0015 amendment of
2026-11-03). Default off, on, the flag winning over it, another user untouched, row security, the
route's input, and that Disconnect never depends on it. The in-process fakes only (``mowlfake``,
``swedebeatcore``); never the network."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from authtools import Env, running_env
from classbeat import connections
from classbeat.auth import ratelimit
from classbeat.connections.preferences import (
    set_uses_swedebeat,
    swedebeat_on,
    uses_swedebeat,
    uses_swedebeat_in,
)
from classbeat.db import user_session
from ctx import make_context
from jobapi import ORIGIN, Browser, session_cookie
from mowlfake import FakeMowl
from optools import TOKEN_A
from preftools import use_swedebeat
from swedebeatcore import FakeSwedebeat
from swedebeatfake import EMAIL, PASSWORD

pytestmark = [pytest.mark.usefixtures("jenv", "mowl_api", "swedebeat_api")]

PREFERENCE = "/api/connections/swedebeat/preference"
CONNECT = "/api/connections/swedebeat"
MIXES = "/api/music/swedebeat-mixes"
LIST = "POST /v1/swedebeat/Playlists"


@pytest.fixture
async def env(
    clean_auth: None, app_database_url: str, swedebeat_app: dict[str, str]
) -> AsyncIterator[Env]:
    """Swedebeat switched on for the server (environment and no admin row) with the test key."""
    async with running_env(
        app_database_url, swedebeat_enabled=True, swedebeat_app=SecretStr(json.dumps(swedebeat_app))
    ) as running:
        yield running


@pytest.fixture(autouse=True)
def clean_flag(superuser: Any) -> Any:
    superuser.execute(text("DELETE FROM app_settings"))
    superuser.commit()
    yield
    superuser.execute(text("DELETE FROM app_settings"))
    superuser.commit()


def store_flag(superuser: Any, value: str) -> None:
    superuser.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES ('swedebeat_enabled', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"v": json.dumps(value)},
    )
    superuser.commit()


async def put(browser: Browser, body: Any) -> httpx.Response:
    headers = await browser.headers()
    response = await browser.client.put(PREFERENCE, headers=headers, json=body)
    browser.client.cookies.clear()
    return response


async def delete(browser: Browser, path: str) -> httpx.Response:
    headers = await browser.headers()
    response = await browser.client.delete(path, headers=headers)
    browser.client.cookies.clear()
    return response


async def rider(browsers: Any, env: Env) -> Browser:
    browser: Browser = await browsers(mowl=False)
    await connections.save(
        env.factory, env.app.state.keyring, make_context(browser.user_id), "mowl",
        {"token": TOKEN_A},
    )  # fmt: skip
    return browser


def rows(superuser: Any) -> list[Any]:
    return list(
        superuser.execute(text("SELECT owner_id, uses_swedebeat FROM user_preferences")).all()
    )


async def access(browser: Browser) -> dict[str, Any]:
    answer = await browser.get("/api/connections")
    assert answer.status_code == 200, answer.text
    block: dict[str, Any] = answer.json()["swedebeat"]
    return block


# --- default off --------------------------------------------------------------------------------


async def test_by_default_the_preference_is_off_and_nothing_is_written_or_called(
    browsers: Any,
    env: Env,
    mowl_api: FakeMowl,
    swedebeat_api: FakeSwedebeat,
    superuser: Any,
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Sunday Climb", token=TOKEN_A)
    assert await access(browser) == {"enabled": True, "available": True, "uses_swedebeat": False}
    listed = await browser.get(MIXES)
    assert (listed.status_code, listed.json()) == (404, {"detail": {"reason": "feature_off"}})
    connected = await browser.post(CONNECT, {"email": EMAIL, "password": PASSWORD})
    assert (connected.status_code, connected.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert mowl_api.requests == []  # no MOWL call for the listing
    assert swedebeat_api.logins == []  # and nothing reached Swedebeat
    assert rows(superuser) == []  # reading never writes a row
    held = "SELECT count(*) FROM connections WHERE provider = 'swedebeat'"
    assert superuser.execute(text(held)).scalar_one() == 0
    assert await uses_swedebeat(env.factory, make_context(browser.user_id)) is False


async def test_preference_off_spends_no_rate_limit_slot_of_the_mixes_listing(
    browsers: Any, env: Env, mowl_api: FakeMowl
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    for _ in range(ratelimit.SWEDEBEAT_MIXES_USER.max_count + 1):
        assert (await browser.get(MIXES)).status_code == 404
    assert mowl_api.requests == []
    await use_swedebeat(env.factory, browser.user_id)
    assert (await browser.get(MIXES)).status_code == 200  # not 429: no slot was taken


# --- on -----------------------------------------------------------------------------------------


async def test_turned_on_the_listing_and_connect_work_as_before(
    browsers: Any, env: Env, mowl_api: FakeMowl, swedebeat_api: FakeSwedebeat, superuser: Any
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Sunday Climb", token=TOKEN_A, listed_seconds=1830)
    stored = await put(browser, {"uses_swedebeat": True})
    assert (stored.status_code, stored.json()) == (200, {"uses_swedebeat": True})
    assert rows(superuser) == [(browser.user_id, True)]
    assert (await access(browser))["uses_swedebeat"] is True
    listed = await browser.get(MIXES)
    assert listed.status_code == 200, listed.text
    assert [m["name"] for m in listed.json()["mixes"]] == ["Sunday Climb"]
    assert mowl_api.requests == [LIST]
    connected = await browser.post(CONNECT, {"email": EMAIL, "password": PASSWORD})
    assert connected.status_code == 200, connected.text
    assert len(swedebeat_api.logins) == 1
    # Storing it again updates the one row.
    assert (await put(browser, {"uses_swedebeat": True})).status_code == 200
    assert rows(superuser) == [(browser.user_id, True)]


# --- the flag wins ------------------------------------------------------------------------------


@pytest.mark.parametrize("off", ["database", "environment"])
async def test_the_flag_off_wins_over_the_preference(
    browsers: Any,
    env: Env,
    mowl_api: FakeMowl,
    swedebeat_api: FakeSwedebeat,
    superuser: Any,
    off: str,
) -> None:
    browser = await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    assert (await put(browser, {"uses_swedebeat": True})).status_code == 200
    if off == "database":
        store_flag(superuser, "off")
    else:
        env.app.state.settings = env.settings.model_copy(update={"swedebeat_enabled": False})
    assert (await browser.get(MIXES)).status_code == 404
    refused = await browser.post(CONNECT, {"email": EMAIL, "password": PASSWORD})
    assert (refused.status_code, refused.json()) == (404, {"detail": {"reason": "feature_off"}})
    assert mowl_api.requests == []
    assert swedebeat_api.logins == []
    assert await access(browser) == {
        "enabled": False, "available": True, "uses_swedebeat": True,
    }  # fmt: skip
    ctx = make_context(browser.user_id)
    assert await swedebeat_on(env.app.state.settings, env.factory, ctx) is False


async def test_storing_the_preference_while_the_flag_is_off_turns_nothing_on(
    browsers: Any, env: Env, mowl_api: FakeMowl, superuser: Any
) -> None:
    store_flag(superuser, "off")
    browser = await rider(browsers, env)
    assert (await put(browser, {"uses_swedebeat": True})).status_code == 200
    assert (await browser.get(MIXES)).status_code == 404
    assert mowl_api.requests == []


# --- another user, row security -----------------------------------------------------------------


async def test_one_users_preference_does_not_touch_another_user(
    browsers: Any, env: Env, mowl_api: FakeMowl, superuser: Any
) -> None:
    alice, bob = await rider(browsers, env), await rider(browsers, env)
    mowl_api.add_mix("Mix", token=TOKEN_A)
    assert (await put(alice, {"uses_swedebeat": True})).status_code == 200
    assert (await access(bob))["uses_swedebeat"] is False
    assert (await bob.get(MIXES)).status_code == 404
    assert (await alice.get(MIXES)).status_code == 200
    # Bob turning his off writes his own row and leaves Alice's as it is.
    assert (await put(bob, {"uses_swedebeat": False})).status_code == 200
    assert sorted(rows(superuser), key=lambda r: str(r[0])) == sorted(
        [(alice.user_id, True), (bob.user_id, False)], key=lambda r: str(r[0])
    )
    # Row security: in Bob's session Alice's row does not exist, even asked for by her id.
    async with user_session(env.factory, make_context(bob.user_id)) as db:
        assert await uses_swedebeat_in(db, alice.user_id) is False
        seen = await db.execute(text("SELECT owner_id FROM user_preferences"))
        assert [r[0] for r in seen.all()] == [bob.user_id]
        changed = await db.execute(
            text("UPDATE user_preferences SET uses_swedebeat = false RETURNING owner_id")
        )
        assert [r[0] for r in changed.all()] == [bob.user_id]
    assert (alice.user_id, True) in rows(superuser)


async def test_a_session_cannot_write_a_row_for_another_user(browsers: Any, env: Env) -> None:
    alice, bob = await rider(browsers, env), await rider(browsers, env)
    with pytest.raises(DBAPIError):
        async with user_session(env.factory, make_context(bob.user_id)) as db:
            await db.execute(
                text("INSERT INTO user_preferences (owner_id, uses_swedebeat) VALUES (:o, true)"),
                {"o": alice.user_id},
            )
    assert await uses_swedebeat(env.factory, make_context(alice.user_id)) is False
    assert await set_uses_swedebeat(env.factory, make_context(bob.user_id), True) is True
    assert await uses_swedebeat(env.factory, make_context(alice.user_id)) is False


# --- Disconnect never depends on it --------------------------------------------------------------


async def test_turned_off_the_stored_connection_still_shows_and_disconnects(
    browsers: Any, env: Env, swedebeat_api: FakeSwedebeat, superuser: Any
) -> None:
    browser = await rider(browsers, env)
    assert (await put(browser, {"uses_swedebeat": True})).status_code == 200
    assert (await browser.post(CONNECT, {"email": EMAIL, "password": PASSWORD})).status_code == 200
    assert (await put(browser, {"uses_swedebeat": False})).status_code == 200
    listed = (await browser.get("/api/connections")).json()
    assert [c["provider"] for c in listed["connections"]].count("swedebeat") == 1
    assert listed["swedebeat"]["uses_swedebeat"] is False
    assert (await browser.post(CONNECT, {"email": EMAIL, "password": PASSWORD})).status_code == 404
    assert len(swedebeat_api.logins) == 1
    assert (await delete(browser, CONNECT)).status_code == 204  # always allowed
    left = superuser.execute(
        text("SELECT count(*) FROM connections WHERE owner_id = :o AND provider = 'swedebeat'"),
        {"o": browser.user_id},
    ).scalar_one()
    assert left == 0


# --- the route's input and who may call it -------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"uses_swedebeat": "yes"},
        {"uses_swedebeat": 1},
        {"uses_swedebeat": None},
        {},
        {"uses_swedebeat": True, "owner_id": "00000000-0000-0000-0000-000000000000"},
        [True],
    ],
)
async def test_only_a_plain_boolean_is_accepted(
    browsers: Any, env: Env, superuser: Any, body: Any
) -> None:
    browser = await rider(browsers, env)
    assert (await put(browser, body)).status_code == 422
    assert rows(superuser) == []


async def test_signed_out_and_cross_site_writes_are_refused(
    browsers: Any, env: Env, superuser: Any
) -> None:
    browser = await rider(browsers, env)
    anonymous = await browser.client.put(
        PREFERENCE, headers={"Origin": ORIGIN}, json={"uses_swedebeat": True}
    )
    assert anonymous.status_code in (401, 403)
    no_csrf = await browser.client.put(
        PREFERENCE,
        headers={"Origin": ORIGIN} | session_cookie(browser.token),
        json={"uses_swedebeat": True},
    )
    browser.client.cookies.clear()
    assert no_csrf.status_code == 403
    assert rows(superuser) == []
