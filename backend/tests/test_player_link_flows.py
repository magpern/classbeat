"""The callback's flow rules (ADR 0034 section 3, ADR 0009 section 4; #1059 criterion 3): a
state is used once, only by the session and the user that started it, and only within its 10
minutes; an ``error``, a missing code or a request away from the fixed callback ends the caller's
own flow without a call to the player."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import session_cookie
from playertools import CALLBACK, START, Linker


def token_calls(linker: Linker) -> int:
    return sum(1 for r in linker.fake.seen if r.url.path == "/api/source/token")


async def test_a_state_is_used_once(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    query = linker.fake.approve(await linker.start(alice))
    assert await linker.callback(alice, query) == {"result": "connected"}
    assert await linker.callback(alice, query) == {"result": "access_denied"}
    assert token_calls(linker) == 1
    assert (await linker.connection(linker.user_id(alice))).generation == 1


async def test_another_session_of_the_same_user_is_refused_and_the_flow_survives(
    linker: Linker,
) -> None:
    alice = await linker.actor("alice@example.com")
    other = await linker.browser(linker.user_id(alice))
    query = linker.fake.approve(await linker.start(alice))
    assert await linker.callback(other, query) == {"result": "access_denied"}
    assert token_calls(linker) == 0
    assert await linker.connection(linker.user_id(alice)) is None
    assert await linker.callback(alice, query) == {"result": "connected"}


async def test_another_user_is_refused_and_the_flow_survives(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    bob = await linker.actor("bob@example.com")
    query = linker.fake.approve(await linker.start(alice))
    assert await linker.callback(bob, query) == {"result": "access_denied"}
    assert token_calls(linker) == 0
    assert await linker.connection(linker.user_id(bob)) is None
    assert len(await linker.flows(linker.user_id(alice))) == 1


async def test_an_expired_flow_is_refused_and_deleted(
    linker: Linker, player_admin: Connection
) -> None:
    alice = await linker.actor("alice@example.com")
    query = linker.fake.approve(await linker.start(alice))
    player_admin.execute(
        text(
            "UPDATE player_link_flows SET created_at = now() - interval '11 minutes', "
            "expires_at = now() - interval '1 minute'"
        )
    )
    assert await linker.callback(alice, query) == {"result": "access_denied"}
    assert token_calls(linker) == 0
    assert await linker.flows(linker.user_id(alice)) == []


async def test_without_a_session_nothing_is_called_or_deleted(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    query = linker.fake.approve(await linker.start(alice))
    response = await alice.client.get(CALLBACK, params=query)
    assert response.status_code == 302
    assert response.headers["location"] == "/account/player?result=access_denied"
    assert token_calls(linker) == 0
    assert len(await linker.flows(linker.user_id(alice))) == 1


async def test_an_error_or_a_missing_code_ends_the_own_flow_without_a_call(
    linker: Linker,
) -> None:
    alice = await linker.actor("alice@example.com")
    for query in (
        {**linker.fake.approve(await linker.start(alice)), "error": "access_denied"},
        {"state": linker.fake.approve(await linker.start(alice))["state"]},
    ):
        assert await linker.callback(alice, query) == {"result": "access_denied"}
    assert token_calls(linker) == 0
    assert await linker.flows(linker.user_id(alice)) == []


async def test_a_code_that_is_not_a_link_code_is_never_sent(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    query = linker.fake.approve(await linker.start(alice))
    query["code"] = query["code"].replace("cbp_lc_", "cbp_src_")
    assert await linker.callback(alice, query) == {"result": "access_denied"}
    assert token_calls(linker) == 0


async def test_a_repeated_parameter_is_refused(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    query = linker.fake.approve(await linker.start(alice))
    response = await alice.client.get(
        f"{CALLBACK}?code={query['code']}&state={query['state']}&state={query['state']}",
        headers=session_cookie(alice.token),
    )
    assert response.headers["location"] == "/account/player?result=access_denied"
    assert token_calls(linker) == 0


async def test_at_most_three_open_flows_per_user(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    first = linker.fake.approve(await linker.start(alice))
    for _ in range(3):
        await linker.start(alice)
    assert len(await linker.flows(linker.user_id(alice))) == 3
    assert await linker.callback(alice, first) == {"result": "access_denied"}


@pytest.mark.usefixtures("frozen_rate_windows")
async def test_ten_starts_per_user_and_hour(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    for _ in range(10):
        await linker.start(alice)
    refused = await alice.post(START)
    assert refused.status_code == 429
    assert "link_url" not in refused.text
    assert len(await linker.flows(linker.user_id(alice))) == 3
