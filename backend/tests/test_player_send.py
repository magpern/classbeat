"""The send-to-player job against the fake player (ADR 0034 section 5, #1060): every answer the
player can give and the protocol's handling of each, against Postgres as ``classbeat_app`` and
``playersendtools.FakeSource``. Never the network, never a real account.

Covered: applied, and a repeat after a retry; 409 stale within bounds (the counter moves and the
next press uses the number after it) and out of bounds (``internal``, the counter unmoved);
409 conflict; 401 (the connection needs a reconnect); a request that never left (retried with
the same number and key); an uncertain send whose one reconciliation finds the number equal,
lower, or gets no answer.
"""

from uuid import UUID

import pytest

from classbeat.player_publish_policy import NUMBER_LIMIT, operation_key
from playersendtools import Planted, SendEnv

pytestmark = pytest.mark.usefixtures("penv")


async def connected(penv: SendEnv) -> tuple[Planted, str]:
    owner = await penv.user()
    token = await penv.connect(owner)
    return penv.plant(owner), token


def held(penv: SendEnv, planted: Planted) -> int | None:
    found = penv.fake.classes.get(str(planted.class_id))
    return None if found is None else found.number


def last_number(penv: SendEnv, planted: Planted) -> int:
    return int(penv.publication(planted.owner, planted.class_id).last_number)


async def test_applied_sends_the_revision_and_records_it(penv: SendEnv) -> None:
    planted, token = await connected(penv)
    job_id = penv.job(planted)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    [body] = penv.fake.posts
    assert set(body) == {
        "source_class_id", "publication_number", "removed", "content_hash", "document",
    }  # fmt: skip
    assert (body["source_class_id"], body["publication_number"], body["removed"]) == (
        str(planted.class_id), 1, False,
    )  # fmt: skip
    assert penv.fake.bearers == [f"Bearer {token}"]
    [op] = penv.operations(planted.owner)
    assert (op.kind, op.state, op.external_id, op.class_id) == (
        "player_send", "succeeded", "1", planted.class_id,
    )  # fmt: skip
    assert op.idempotency_key == operation_key(planted.class_id, 1)
    assert op.request_context["revision_id"] == str(planted.revision_id)
    assert op.request_context["document_hash"] == body["content_hash"]
    assert token not in str(op.request_context)
    assert "document" not in op.request_context
    row = penv.publication(planted.owner, planted.class_id)
    assert (row.last_number, row.sent_revision_id, row.removed_at) == (
        1, planted.revision_id, None,
    )  # fmt: skip
    assert "sha256:" + bytes(row.sent_document_hash).hex() == body["content_hash"]
    assert held(penv, planted) == 1


async def test_a_second_press_sends_the_next_number(penv: SendEnv) -> None:
    planted, _ = await connected(penv)
    penv.job(planted)
    await penv.run_next()
    changed = {**planted.content, "name": "Thursday climb"}
    second = Planted(
        planted.owner, planted.class_id, penv.revision(planted.owner, planted.class_id, changed),
        changed,
    )  # fmt: skip
    job_id = penv.job(second)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    assert [p["publication_number"] for p in penv.fake.posts] == [1, 2]
    row = penv.publication(planted.owner, planted.class_id)
    assert (row.last_number, row.sent_revision_id) == (2, second.revision_id)


async def test_a_repeat_after_a_retry_succeeds_with_the_same_number_and_key(
    penv: SendEnv,
) -> None:
    planted, _ = await connected(penv)
    # Applied, the answer lost; the reconciliation is told "not found", so the row is failed and
    # retryable; the retry sends the same envelope, which the player answers as a repeat.
    penv.fake.script = ["drop"]
    penv.fake.reads = ["404"]
    job_id = penv.job(planted)
    await penv.run_next()
    assert penv.row(job_id).state == "queued"  # retryable: back in the queue
    [op] = penv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "upstream_unavailable")
    penv.ready(job_id)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    first, again = penv.fake.posts
    assert first == again
    [op] = penv.operations(planted.owner)
    assert (op.state, op.idempotency_key) == ("succeeded", operation_key(planted.class_id, 1))
    assert last_number(penv, planted) == 1


async def test_stale_within_bounds_moves_the_counter_and_the_next_press_follows(
    penv: SendEnv,
) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = ["stale:7"]
    job_id = penv.job(planted)
    await penv.run_next()
    assert (penv.row(job_id).state, penv.row(job_id).error_category) == ("failed", "player_ahead")
    [op] = penv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "player_ahead")
    assert last_number(penv, planted) == 7
    again = penv.job(planted)
    await penv.run_next()
    assert penv.row(again).state == "completed"
    assert [p["publication_number"] for p in penv.fake.posts] == [1, 8]
    assert last_number(penv, planted) == 8


@pytest.mark.parametrize("highest", [1, 0, NUMBER_LIMIT, NUMBER_LIMIT + 5])
async def test_stale_out_of_bounds_is_internal_and_moves_nothing(
    penv: SendEnv, highest: int
) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = [f"stale:{highest}"]
    job_id = penv.job(planted)
    await penv.run_next()
    assert (penv.row(job_id).state, penv.row(job_id).error_category) == ("failed", "internal")
    assert last_number(penv, planted) == 1
    assert len(penv.fake.posts) == 1


async def test_a_conflict_fails_and_is_never_resent(penv: SendEnv) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = ["conflict"]
    job_id = penv.job(planted)
    await penv.run_next()
    row = penv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "player_conflict")
    [op] = penv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "player_conflict")
    assert len(penv.fake.posts) == 1
    assert last_number(penv, planted) == 1  # the number stays used


async def test_401_fails_not_connected_and_the_connection_needs_a_reconnect(
    penv: SendEnv,
) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = ["401"]
    job_id = penv.job(planted)
    await penv.run_next()
    assert (penv.row(job_id).state, penv.row(job_id).error_category) == ("failed", "not_connected")
    assert penv.status(planted.owner) == "needs_reconnect"
    # A later press is refused before anything is sent.
    again = penv.job(planted)
    await penv.run_next()
    assert (penv.row(again).state, penv.row(again).error_category) == ("failed", "not_connected")
    assert len(penv.fake.posts) == 1


async def test_a_request_that_never_left_is_retried_with_the_same_number_and_key(
    penv: SendEnv,
) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = ["refuse"]
    job_id = penv.job(planted)
    await penv.run_next()
    assert penv.row(job_id).state == "queued"  # retryable: back in the queue
    [op] = penv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "upstream_unavailable")
    assert penv.fake.gets == []  # never left: nothing to reconcile
    penv.ready(job_id)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    first, again = penv.fake.posts
    assert first == again
    [op] = penv.operations(planted.owner)  # the failed row was taken over, not duplicated
    assert (op.state, op.idempotency_key) == ("succeeded", operation_key(planted.class_id, 1))
    assert last_number(penv, planted) == 1


async def test_uncertain_and_reconciled_equal_succeeds(penv: SendEnv) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = ["drop"]
    job_id = penv.job(planted)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    assert penv.fake.gets == [str(planted.class_id)]
    [op] = penv.operations(planted.owner)
    assert (op.state, op.resolved_by, op.external_id) == ("succeeded", "worker", "1")
    assert penv.publication(planted.owner, planted.class_id).sent_revision_id == (
        planted.revision_id
    )


async def test_uncertain_and_reconciled_lower_is_retried_with_the_same_number(
    penv: SendEnv,
) -> None:
    planted, _ = await connected(penv)
    penv.job(planted)
    await penv.run_next()  # the player holds 1
    penv.fake.script = ["lost"]  # number 2 never applied, its answer lost
    job_id = penv.job(planted)
    await penv.run_next()
    assert penv.row(job_id).state == "queued"  # retryable: back in the queue
    ops = penv.operations(planted.owner)
    assert (ops[-1].state, ops[-1].idempotency_key) == (
        "failed", operation_key(planted.class_id, 2),
    )  # fmt: skip
    penv.ready(job_id)
    await penv.run_next()
    assert penv.row(job_id).state == "completed"
    assert [p["publication_number"] for p in penv.fake.posts] == [1, 2, 2]
    assert held(penv, planted) == 2
    assert last_number(penv, planted) == 2


async def test_uncertain_with_no_answer_stays_uncertain_and_blocks_the_next_press(
    penv: SendEnv,
) -> None:
    planted, _ = await connected(penv)
    penv.fake.script = ["drop"]
    penv.fake.reads = ["none"]
    job_id = penv.job(planted)
    await penv.run_next()
    row = penv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [op] = penv.operations(planted.owner)
    assert op.state == "uncertain"
    again = penv.job(planted)
    await penv.run_next()
    assert (penv.row(again).state, penv.row(again).error_category) == (
        "failed", "uncertain_write",
    )  # fmt: skip
    assert len(penv.fake.posts) == 1  # never sent again automatically


async def test_a_disconnected_player_sends_nothing(penv: SendEnv) -> None:
    planted, _ = await connected(penv)
    penv.set_status(planted.owner, "needs_reconnect")
    job_id = penv.job(planted)
    await penv.run_next()
    assert (penv.row(job_id).state, penv.row(job_id).error_category) == ("failed", "not_connected")
    assert penv.fake.posts == []


def test_the_operation_key_names_the_class_and_number() -> None:
    class_id = UUID(int=7)
    assert operation_key(class_id, 3) == f"player:{class_id}:3"
