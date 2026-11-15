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

pytestmark = pytest.mark.usefixtures("penv")


async def sent(penv: SendEnv) -> Planted:
    owner = await penv.user()
    await penv.connect(owner)
    planted = penv.plant(owner)
    job_id = penv.job(planted)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    return planted


async def delete(penv: SendEnv, planted: Planted) -> None:
    ctx = make_context(planted.owner)
    outcome = await service.delete(penv.factory, ctx, planted.class_id, confirm_uncertain=False)
    assert outcome.reason is None


def removals(penv: SendEnv, owner: UUID) -> list[Any]:
    return penv.sql(
        "SELECT * FROM jobs WHERE owner_id = :o AND kind = 'player_remove' ORDER BY created_at",
        o=owner,
    ).all()


def class_rows(penv: SendEnv, planted: Planted) -> int:
    return penv.sql("SELECT count(*) FROM classes WHERE id = :c", c=planted.class_id).scalar_one()


async def test_deleting_a_sent_class_enqueues_a_removal_that_survives_the_cascade(
    penv: SendEnv,
) -> None:
    planted = await sent(penv)
    await delete(penv, planted)
    assert class_rows(penv, planted) == 0
    [job] = removals(penv, planted.owner)
    assert (job.state, job.class_id, job.credit_state) == ("queued", None, "none")
    assert job.input == {"source_class_id": str(planted.class_id), "number": 2}
    assert [kind for _, kind, _, _ in penv.events(job.id)] == ["queued"]
    row = penv.publication(planted.owner, planted.class_id)
    assert row.last_number == 2
    assert row.removed_at is not None
    # The send's own job went with the class; the removal did not.
    assert penv.sql(
        "SELECT count(*) FROM jobs WHERE owner_id = :o AND kind = 'send_to_player'",
        o=planted.owner,
    ).scalar_one() == 0  # fmt: skip


async def test_the_removal_sends_removed_true_and_no_document(penv: SendEnv) -> None:
    planted = await sent(penv)
    await delete(penv, planted)
    [job] = removals(penv, planted.owner)
    await penv.run_next()
    assert penv.row(job.id).state == "completed"
    assert penv.fake.posts[-1] == {
        "source_class_id": str(planted.class_id), "publication_number": 2, "removed": True,
    }  # fmt: skip
    found = penv.fake.classes[str(planted.class_id)]
    assert (found.number, found.removed) == (2, True)
    [op] = [o for o in penv.operations(planted.owner) if o.kind == "player_remove"]
    assert (op.state, op.class_id, op.job_id) == ("succeeded", None, job.id)
    assert op.idempotency_key == operation_key(planted.class_id, 2)
    assert op.request_context == {"source_class_id": str(planted.class_id), "publication_number": 2}


async def test_an_uncertain_removal_reconciles_on_removed(penv: SendEnv) -> None:
    planted = await sent(penv)
    await delete(penv, planted)
    [job] = removals(penv, planted.owner)
    penv.fake.script = ["drop"]
    await penv.run_next()
    assert penv.row(job.id).state == "completed"
    [op] = [o for o in penv.operations(planted.owner) if o.kind == "player_remove"]
    assert (op.state, op.resolved_by) == ("succeeded", "worker")


async def test_a_removal_never_left_is_retried_with_the_same_number(penv: SendEnv) -> None:
    planted = await sent(penv)
    await delete(penv, planted)
    [job] = removals(penv, planted.owner)
    penv.fake.script = ["refuse"]
    await penv.run_next()
    assert penv.row(job.id).state == "queued"
    penv.ready(job.id)
    await penv.run_next()
    assert penv.row(job.id).state == "completed"
    assert [p["publication_number"] for p in penv.fake.posts] == [1, 2, 2]


@pytest.mark.parametrize("status", ["needs_reconnect", "gone"])
async def test_deleting_while_not_connected_sends_nothing(penv: SendEnv, status: str) -> None:
    planted = await sent(penv)
    if status == "gone":
        penv.sql("DELETE FROM connections WHERE owner_id = :o AND provider = 'player'",
                 o=planted.owner)  # fmt: skip
    else:
        penv.set_status(planted.owner, status)
    posted = len(penv.fake.posts)
    await delete(penv, planted)
    assert class_rows(penv, planted) == 0
    assert removals(penv, planted.owner) == []
    assert penv.publication(planted.owner, planted.class_id) is None
    assert len(penv.fake.posts) == posted


async def test_deleting_a_class_never_sent_enqueues_nothing(penv: SendEnv) -> None:
    owner = await penv.user()
    await penv.connect(owner)
    planted = penv.plant(owner)
    await delete(penv, planted)
    assert removals(penv, owner) == []
    assert penv.publication(owner, planted.class_id) is None


async def test_a_removal_whose_connection_needs_a_reconnect_sends_nothing(
    penv: SendEnv,
) -> None:
    planted = await sent(penv)
    await delete(penv, planted)
    [job] = removals(penv, planted.owner)
    penv.set_status(planted.owner, "needs_reconnect")
    await penv.run_next()
    row = penv.row(job.id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert len(penv.fake.posts) == 1


async def test_a_removal_planted_with_another_number_sends_nothing(penv: SendEnv) -> None:
    """The decide step checks the row is still removed with the job's own number."""
    planted = await sent(penv)
    await delete(penv, planted)
    [job] = removals(penv, planted.owner)
    penv.sql(
        "UPDATE jobs SET input = CAST(:i AS jsonb) WHERE id = :id",
        i=json.dumps({"source_class_id": str(planted.class_id), "number": 5}), id=job.id,
    )  # fmt: skip
    await penv.run_next()
    row = penv.row(job.id)
    assert (row.state, row.error_category) == ("failed", "internal")
    assert len(penv.fake.posts) == 1
