"""What the player holds of a class, and the class delete's answer (ADR 0034 section 6, #1068),
against Postgres as ``classbeat_app`` and the fake player (``playersendtools``). Never the
network, never a real account.

Covered: ``GET /api/classes/{id}``'s ``player`` for never sent, sent, sent and then edited,
another owner's class (404) and a player that is not connected; the delete answer's
``player_removal_started`` and ``player_removal_job_id``; a delete of a class never sent takes no
lock on the connection row and starts no job.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from authtools import ORIGIN
from classbeat.classes import removal, service
from classbeat.classes.schemas import DeletedOut
from classbeat.db import user_session
from classbeat.player_send.class_state import sent_state
from ctx import make_context
from jobapi import Browser
from playersendtools import Planted, SendEnv

pytestmark = pytest.mark.usefixtures("sendenv")


async def sent(sendenv: SendEnv) -> Planted:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    sendenv.job(planted)
    await sendenv.run_next()
    return planted


async def player(sendenv: SendEnv, planted: Planted, owner: UUID | None = None) -> Any:
    ctx = make_context(planted.owner if owner is None else owner)
    found = await service.get_class_detail(sendenv.factory, ctx, planted.class_id)
    return None if found is None else found.player


async def test_a_class_never_sent_has_no_player_state(sendenv: SendEnv) -> None:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    assert await player(sendenv, planted) is None


async def test_a_sent_class_names_the_revision_the_player_holds(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    state = await player(sendenv, planted)
    assert (state.sent_revision_id, state.sent_revision_number) == (planted.revision_id, 1)


async def test_a_class_edited_after_the_send_still_names_the_sent_revision(
    sendenv: SendEnv,
) -> None:
    planted = await sent(sendenv)
    newer = sendenv.revision(planted.owner, planted.class_id, planted.content)
    sendenv.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c", r=newer, c=planted.class_id
    )
    found = await service.get_class_detail(
        sendenv.factory, make_context(planted.owner), planted.class_id
    )
    assert found is not None
    assert found.row.current_revision_number == 2
    assert found.player is not None
    assert (found.player.sent_revision_id, found.player.sent_revision_number) == (
        planted.revision_id,
        1,
    )


async def test_another_owners_class_is_not_visible(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    stranger = await sendenv.user()
    await sendenv.connect(stranger)
    ctx = make_context(stranger)
    assert await service.get_class_detail(sendenv.factory, ctx, planted.class_id) is None
    assert await player(sendenv, planted, stranger) is None


@pytest.mark.parametrize("status", ["needs_reconnect", "gone"])
async def test_a_player_that_is_not_connected_gives_no_state(sendenv: SendEnv, status: str) -> None:
    planted = await sent(sendenv)
    if status == "gone":
        sendenv.sql(
            "DELETE FROM connections WHERE owner_id = :o AND provider = 'player'", o=planted.owner
        )
    else:
        sendenv.set_status(planted.owner, status)
    assert await player(sendenv, planted) is None


async def test_the_delete_answer_names_the_removal_job(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    done = await removal.delete(
        sendenv.factory, make_context(planted.owner), planted.class_id, confirm_uncertain=False
    )
    body = removal.deleted_out(done)
    [job] = sendenv.sql(
        "SELECT id FROM jobs WHERE owner_id = :o AND kind = 'player_remove'", o=planted.owner
    ).all()
    assert (body.player_removal_started, body.player_removal_job_id) == (True, job.id)


async def test_the_delete_answer_of_a_class_never_sent_starts_no_job(sendenv: SendEnv) -> None:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    done = await removal.delete(
        sendenv.factory, make_context(owner), planted.class_id, confirm_uncertain=False
    )
    body: DeletedOut = removal.deleted_out(done)
    assert (body.player_removal_started, body.player_removal_job_id) == (False, None)
    assert sendenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one() == 0


@contextmanager
def connection_lock(sendenv: SendEnv) -> Iterator[None]:
    """A second session holding ``FOR UPDATE`` on every player connection row: anything that
    takes ``FOR SHARE`` on one waits until the block ends."""
    engine = sendenv.admin.engine
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as c:
        c.execute(text("SELECT 1 FROM connections WHERE provider = 'player' FOR UPDATE"))
        try:
            yield
        finally:
            c.rollback()


async def test_deleting_a_class_never_sent_takes_no_lock_on_the_connection_row(
    sendenv: SendEnv,
) -> None:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    with connection_lock(sendenv):
        done = await asyncio.wait_for(
            removal.delete(
                sendenv.factory, make_context(owner), planted.class_id, confirm_uncertain=False
            ),
            timeout=5,
        )
    assert done.reason is None
    assert done.removal_job_id is None


async def test_a_never_numbered_publication_row_goes_with_its_class(sendenv: SendEnv) -> None:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    sendenv.sql(
        "INSERT INTO player_publications (owner_id, source_class_id) VALUES (:o, :c)",
        o=owner,
        c=planted.class_id,
    )
    assert sendenv.publication(owner, planted.class_id).last_number == 0
    done = await removal.delete(
        sendenv.factory, make_context(owner), planted.class_id, confirm_uncertain=False
    )
    assert (done.reason, done.removal_job_id) == (None, None)
    assert sendenv.publication(owner, planted.class_id) is None


async def test_reading_a_class_takes_no_lock_on_the_connection_row(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    with connection_lock(sendenv):
        found = await asyncio.wait_for(
            service.get_class_detail(
                sendenv.factory, make_context(planted.owner), planted.class_id
            ),
            timeout=5,
        )
    assert found is not None
    assert found.player is not None


async def test_sent_state_of_another_owner_sees_nothing(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    stranger = await sendenv.user()
    await sendenv.connect(stranger)
    async with user_session(sendenv.factory, make_context(stranger)) as db:
        assert await sent_state(db, stranger, planted.class_id) is None
    async with user_session(sendenv.factory, make_context(planted.owner)) as db:
        assert await sent_state(db, planted.owner, planted.class_id) is not None


@pytest.fixture
async def browser(sendenv: SendEnv) -> AsyncIterator[Any]:
    clients: list[httpx.AsyncClient] = []

    async def make(owner: UUID) -> Browser:
        issued = await sendenv.app.login(owner)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=sendenv.app.app), base_url=ORIGIN
        )
        clients.append(client)
        return Browser(sendenv.app, issued.token, client, owner)

    yield make
    for client in clients:
        await client.aclose()


async def delete_over_http(web: Browser, class_id: UUID) -> httpx.Response:
    response = await web.client.delete(f"/api/classes/{class_id}", headers=await web.headers())
    web.client.cookies.clear()
    return response


async def test_the_class_read_over_http_names_the_sent_revision(
    sendenv: SendEnv, browser: Any
) -> None:
    planted = await sent(sendenv)
    web = await browser(planted.owner)
    got = await web.get(f"/api/classes/{planted.class_id}")
    assert got.status_code == 200
    assert got.json()["player"] == {
        "sent_revision_id": str(planted.revision_id),
        "sent_revision_number": 1,
    }


async def test_the_delete_answer_over_http_carries_the_removal_keys(
    sendenv: SendEnv, browser: Any
) -> None:
    planted = await sent(sendenv)
    web = await browser(planted.owner)
    got = await delete_over_http(web, planted.class_id)
    [job] = sendenv.sql(
        "SELECT id FROM jobs WHERE owner_id = :o AND kind = 'player_remove'", o=planted.owner
    ).all()
    assert got.status_code == 200
    body = got.json()
    assert body["player_removal_started"] is True
    assert body["player_removal_job_id"] == str(job.id)


async def test_the_delete_answer_over_http_without_a_job(sendenv: SendEnv, browser: Any) -> None:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    web = await browser(owner)
    got = await delete_over_http(web, planted.class_id)
    assert got.status_code == 200
    body = got.json()
    assert body["player_removal_started"] is False
    assert body["player_removal_job_id"] is None
