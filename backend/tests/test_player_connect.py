"""Connect, reconnect and disconnect classbeat-player against a fake player (ADR 0034 section 3,
#1059 criterion 1): the source token is stored encrypted and never logged, never in an answer to
the browser and never in an ``auth_events`` row."""

import logging
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat.log import JsonFormatter
from playertools import CALLBACK, PLAYER, START, STATUS, Linker


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.handler.setFormatter(JsonFormatter())
    caplog.set_level(logging.DEBUG)
    return caplog


async def events(env: Env) -> list[tuple[str, dict[str, Any]]]:
    sql = "SELECT kind, detail FROM auth_events WHERE kind LIKE 'connection_%' ORDER BY at, id"
    async with env.db() as db:
        return [(row.kind, row.detail) for row in (await db.execute(text(sql))).all()]


async def test_connect_stores_the_token_encrypted_and_shows_it_nowhere(
    linker: Linker, player_admin: Connection, logs: pytest.LogCaptureFixture
) -> None:
    alice = await linker.actor("alice@example.com")
    link_url = await linker.start(alice)
    assert link_url.startswith(f"{PLAYER}/link/source?")
    query = linker.fake.approve(link_url)
    grant = linker.fake.grants[query["code"]]
    assert grant.login_hint == linker.subject(alice)
    assert grant.redirect_uri == linker.issuer + CALLBACK

    page = await linker.callback(alice, query)
    assert page == {"result": "connected"}
    [issued] = linker.fake.tokens
    row = await linker.connection(linker.user_id(alice))
    assert row.status == "connected"
    assert row.public == {}
    assert issued.encode() not in bytes(row.secret)
    assert linker.fake.ended == []

    status = await alice.get(STATUS)
    assert status.json()["status"] == "connected"
    assert status.json()["link_enabled"] is True
    everything = player_admin.execute(text("SELECT row_to_json(c)::text FROM connections c"))
    dumped = " ".join(r[0] for r in everything)
    for leaked in (issued, query["code"], query["state"]):
        assert leaked not in logs.text
        assert leaked not in status.text
        assert leaked not in dumped
    assert str(await events(linker.env)) == str([("connection_connected", {"provider": "player"})])
    assert await linker.flows(linker.user_id(alice)) == []


async def test_reconnect_ends_the_old_link_and_replaces_the_row(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    first = await linker.connection(linker.user_id(alice))
    old = linker.fake.tokens[0]
    assert await linker.connect(alice) == {"result": "connected"}
    second = await linker.connection(linker.user_id(alice))
    assert linker.fake.ended == [old]
    assert (second.id, second.generation) == (first.id, first.generation + 1)
    assert bytes(second.secret) != bytes(first.secret)


async def test_a_failed_old_delete_still_reconnects_with_a_note(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    linker.fake.delete_status = 503
    assert await linker.connect(alice) == {"result": "connected", "note": "old_link_may_remain"}
    assert len(linker.fake.ended) == 1
    assert (await linker.connection(linker.user_id(alice))).status == "connected"


async def test_disconnect_ends_the_link_and_deletes_the_row(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    answer = await alice.call("DELETE", STATUS)
    assert answer.json() == {"disconnected": True, "link_may_remain": False}
    assert linker.fake.ended == linker.fake.tokens
    assert await linker.connection(linker.user_id(alice)) is None
    assert linker.fake.tokens[0] not in answer.text
    assert [kind for kind, _ in await events(linker.env)] == [
        "connection_connected",
        "connection_disconnected",
    ]
    again = await alice.call("DELETE", STATUS)
    assert again.json() == {"disconnected": False, "link_may_remain": False}


@pytest.mark.parametrize(
    ("status", "down", "may_remain"), [(401, False, False), (500, False, True), (204, True, True)]
)
async def test_disconnect_deletes_the_row_whatever_the_player_answers(
    linker: Linker, status: int, down: bool, may_remain: bool
) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    linker.fake.delete_status, linker.fake.down = status, down
    answer = await alice.call("DELETE", STATUS)
    assert answer.json() == {"disconnected": True, "link_may_remain": may_remain}
    assert await linker.connection(linker.user_id(alice)) is None


async def test_without_a_player_client_linking_is_off_and_disconnect_still_works(
    linker: Linker,
) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    del linker.env.app.state.player_source
    assert (await alice.post(START)).status_code == 404
    assert (await alice.get(STATUS)).json()["link_enabled"] is False
    answer = await alice.call("DELETE", STATUS)
    assert answer.json() == {"disconnected": True, "link_may_remain": True}
    assert len(linker.fake.ended) == 0


async def test_start_and_disconnect_need_csrf(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    response = await alice.client.post(START, headers={"Origin": "https://evil.example"})
    assert response.status_code in (401, 403)
    assert linker.fake.seen == []
