"""The removal a class's deletion enqueues (ADR 0034 section 5, amending ADR 0005 section 1;
#1060), against Postgres as ``classbeat_app`` and the fake player (``playersendtools``). Never the
network, never a real account.

Covered: deleting a sent class takes the next number, marks the publication row removed and
enqueues one ``player_remove`` job with ``class_id`` NULL that survives the class's cascade; the
job sends ``removed: true`` with no document, and an uncertain removal reconciles on
``removed``; deleting while the player is disconnected, needs a reconnect, or for a class never
sent enqueues and sends nothing (the row is dropped); a removal whose connection went away
meanwhile sends nothing.
"""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import service
from classbeat.player_publish_policy import operation_key
from ctx import make_context
from playersendtools import Planted, SendEnv

pytestmark = pytest.mark.usefixtures("sendenv")


async def sent(sendenv: SendEnv) -> Planted:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    return planted


async def delete(sendenv: SendEnv, planted: Planted) -> None:
    ctx = make_context(planted.owner)
    outcome = await service.delete(sendenv.factory, ctx, planted.class_id, confirm_uncertain=False)
    assert outcome.reason is None


def removals(sendenv: SendEnv, owner: UUID) -> list[Any]:
    return sendenv.sql(
        "SELECT * FROM jobs WHERE owner_id = :o AND kind = 'player_remove' ORDER BY created_at",
        o=owner,
    ).all()


def class_rows(sendenv: SendEnv, planted: Planted) -> int:
    return sendenv.sql(
        "SELECT count(*) FROM classes WHERE id = :c", c=planted.class_id
    ).scalar_one()


async def test_deleting_a_sent_class_enqueues_a_removal_that_survives_the_cascade(
    sendenv: SendEnv,
) -> None:
    planted = await sent(sendenv)
    await delete(sendenv, planted)
    assert class_rows(sendenv, planted) == 0
    [job] = removals(sendenv, planted.owner)
    assert (job.state, job.class_id, job.credit_state) == ("queued", None, "none")
    assert job.input == {"source_class_id": str(planted.class_id), "number": 2}
    assert [kind for _, kind, _, _ in sendenv.events(job.id)] == ["queued"]
    row = sendenv.publication(planted.owner, planted.class_id)
    assert row.last_number == 2
    assert row.removed_at is not None
    # The send's own job went with the class; the removal did not.
    assert sendenv.sql(
        "SELECT count(*) FROM jobs WHERE owner_id = :o AND kind = 'send_to_player'",
        o=planted.owner,
    ).scalar_one() == 0  # fmt: skip


async def test_the_removal_sends_removed_true_and_no_document(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    await delete(sendenv, planted)
    [job] = removals(sendenv, planted.owner)
    await sendenv.run_next()
    assert sendenv.row(job.id).state == "completed"
    assert sendenv.fake.posts[-1] == {
        "source_class_id": str(planted.class_id), "publication_number": 2, "removed": True,
    }  # fmt: skip
    found = sendenv.fake.classes[str(planted.class_id)]
    assert (found.number, found.removed) == (2, True)
    [op] = [o for o in sendenv.operations(planted.owner) if o.kind == "player_remove"]
    assert (op.state, op.class_id, op.job_id) == ("succeeded", None, job.id)
    assert op.idempotency_key == operation_key(planted.class_id, 2)
    assert op.request_context == {"source_class_id": str(planted.class_id), "publication_number": 2}


async def test_an_uncertain_removal_reconciles_on_removed(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    await delete(sendenv, planted)
    [job] = removals(sendenv, planted.owner)
    sendenv.fake.script = ["drop"]
    await sendenv.run_next()
    assert sendenv.row(job.id).state == "completed"
    [op] = [o for o in sendenv.operations(planted.owner) if o.kind == "player_remove"]
    assert (op.state, op.resolved_by) == ("succeeded", "worker")


async def test_a_removal_never_left_is_retried_with_the_same_number(sendenv: SendEnv) -> None:
    planted = await sent(sendenv)
    await delete(sendenv, planted)
    [job] = removals(sendenv, planted.owner)
    sendenv.fake.script = ["refuse"]
    await sendenv.run_next()
    assert sendenv.row(job.id).state == "queued"
    sendenv.ready(job.id)
    await sendenv.run_next()
    assert sendenv.row(job.id).state == "completed"
    assert [p["publication_number"] for p in sendenv.fake.posts] == [1, 2, 2]


@pytest.mark.parametrize("status", ["needs_reconnect", "gone"])
async def test_deleting_while_not_connected_sends_nothing(sendenv: SendEnv, status: str) -> None:
    planted = await sent(sendenv)
    if status == "gone":
        sendenv.sql("DELETE FROM connections WHERE owner_id = :o AND provider = 'player'",
                 o=planted.owner)  # fmt: skip
    else:
        sendenv.set_status(planted.owner, status)
    posted = len(sendenv.fake.posts)
    await delete(sendenv, planted)
    assert class_rows(sendenv, planted) == 0
    assert removals(sendenv, planted.owner) == []
    assert sendenv.publication(planted.owner, planted.class_id) is None
    assert len(sendenv.fake.posts) == posted


async def test_deleting_a_class_never_sent_enqueues_nothing(sendenv: SendEnv) -> None:
    owner = await sendenv.user()
    await sendenv.connect(owner)
    planted = sendenv.plant(owner)
    await delete(sendenv, planted)
    assert removals(sendenv, owner) == []
    assert sendenv.publication(owner, planted.class_id) is None


async def test_a_removal_whose_connection_needs_a_reconnect_sends_nothing(
    sendenv: SendEnv,
) -> None:
    planted = await sent(sendenv)
    await delete(sendenv, planted)
    [job] = removals(sendenv, planted.owner)
    sendenv.set_status(planted.owner, "needs_reconnect")
    await sendenv.run_next()
    row = sendenv.row(job.id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert len(sendenv.fake.posts) == 1


async def test_a_removal_planted_with_another_number_sends_nothing(sendenv: SendEnv) -> None:
    """The decide step checks the row is still removed with the job's own number."""
    planted = await sent(sendenv)
    await delete(sendenv, planted)
    [job] = removals(sendenv, planted.owner)
    sendenv.sql(
        "UPDATE jobs SET input = CAST(:i AS jsonb) WHERE id = :id",
        i=json.dumps({"source_class_id": str(planted.class_id), "number": 5}), id=job.id,
    )  # fmt: skip
    await sendenv.run_next()
    row = sendenv.row(job.id)
    assert (row.state, row.error_category) == ("failed", "internal")
    assert len(sendenv.fake.posts) == 1
