"""What the player holds of a class, and the class delete's answer (ADR 0034 section 6, #1068),
against Postgres as ``classbeat_app`` and the fake player (``playersendtools``). Never the
network, never a real account.

Covered: ``GET /api/classes/{id}``'s ``player`` for never sent, sent, sent and then edited,
another owner's class (404) and a player that is not connected; the delete answer's
``player_removal_started`` and ``player_removal_job_id``; a delete of a class never sent takes no
lock on the connection row and starts no job.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from classbeat.classes import removal, service
from classbeat.classes.removal import DeletedOut
from ctx import make_context
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
