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

pytestmark = pytest.mark.usefixtures("sendenv")


async def connected(sendenv: SendEnv) -> tuple[Planted, str]:
    owner = await sendenv.user()
    token = await sendenv.connect(owner)
    return sendenv.plant(owner), token


def held(sendenv: SendEnv, planted: Planted) -> int | None:
    found = sendenv.fake.classes.get(str(planted.class_id))
    return None if found is None else found.number


def last_number(sendenv: SendEnv, planted: Planted) -> int:
    return int(sendenv.publication(planted.owner, planted.class_id).last_number)


async def test_applied_sends_the_revision_and_records_it(sendenv: SendEnv) -> None:
    planted, token = await connected(sendenv)
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    [body] = sendenv.fake.posts
    assert set(body) == {
        "source_class_id", "publication_number", "removed", "content_hash", "document",
    }  # fmt: skip
    assert (body["source_class_id"], body["publication_number"], body["removed"]) == (
        str(planted.class_id), 1, False,
    )  # fmt: skip
    assert sendenv.fake.bearers == [f"Bearer {token}"]
    [op] = sendenv.operations(planted.owner)
    assert (op.kind, op.state, op.external_id, op.class_id) == (
        "player_send", "succeeded", "1", planted.class_id,
    )  # fmt: skip
    assert op.idempotency_key == operation_key(planted.class_id, 1)
    assert op.request_context["revision_id"] == str(planted.revision_id)
    assert op.request_context["document_hash"] == body["content_hash"]
    assert token not in str(op.request_context)
    assert "document" not in op.request_context
    row = sendenv.publication(planted.owner, planted.class_id)
    assert (row.last_number, row.sent_revision_id, row.removed_at) == (
        1, planted.revision_id, None,
    )  # fmt: skip
    assert "sha256:" + bytes(row.sent_document_hash).hex() == body["content_hash"]
    assert held(sendenv, planted) == 1


async def test_a_second_press_sends_the_next_number(sendenv: SendEnv) -> None:
    planted, _ = await connected(sendenv)
    sendenv.job(planted)
    await sendenv.run_next()
    changed = {**planted.content, "name": "Thursday climb"}
    second = Planted(
        planted.owner, planted.class_id, sendenv.revision(planted.owner, planted.class_id, changed),
        changed,
    )  # fmt: skip
    job_id = sendenv.job(second)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    assert [p["publication_number"] for p in sendenv.fake.posts] == [1, 2]
    row = sendenv.publication(planted.owner, planted.class_id)
    assert (row.last_number, row.sent_revision_id) == (2, second.revision_id)


async def test_a_repeat_after_a_retry_succeeds_with_the_same_number_and_key(
    sendenv: SendEnv,
) -> None:
    planted, _ = await connected(sendenv)
    # Applied, the answer lost; the reconciliation is told "not found", so the row is failed and
    # retryable; the retry sends the same envelope, which the player answers as a repeat.
    sendenv.fake.script = ["drop"]
    sendenv.fake.reads = ["404"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "queued"  # retryable: back in the queue
    [op] = sendenv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "upstream_unavailable")
    sendenv.ready(job_id)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    first, again = sendenv.fake.posts
    assert first == again
    [op] = sendenv.operations(planted.owner)
    assert (op.state, op.idempotency_key) == ("succeeded", operation_key(planted.class_id, 1))
    assert last_number(sendenv, planted) == 1


async def test_stale_within_bounds_moves_the_counter_and_the_next_press_follows(
    sendenv: SendEnv,
) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = ["stale:7"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert (sendenv.row(job_id).state, sendenv.row(job_id).error_category) == (
        "failed",
        "player_ahead",
    )
    [op] = sendenv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "player_ahead")
    assert last_number(sendenv, planted) == 7
    again = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(again).state == "completed"
    assert [p["publication_number"] for p in sendenv.fake.posts] == [1, 8]
    assert last_number(sendenv, planted) == 8


@pytest.mark.parametrize("highest", [1, 0, NUMBER_LIMIT, NUMBER_LIMIT + 5])
async def test_stale_out_of_bounds_is_internal_and_moves_nothing(
    sendenv: SendEnv, highest: int
) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = [f"stale:{highest}"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert (sendenv.row(job_id).state, sendenv.row(job_id).error_category) == ("failed", "internal")
    assert last_number(sendenv, planted) == 1
    assert len(sendenv.fake.posts) == 1


async def test_a_conflict_fails_and_is_never_resent(sendenv: SendEnv) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = ["conflict"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    row = sendenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "player_conflict")
    [op] = sendenv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "player_conflict")
    assert len(sendenv.fake.posts) == 1
    assert last_number(sendenv, planted) == 1  # the number stays used


async def test_401_fails_not_connected_and_the_connection_needs_a_reconnect(
    sendenv: SendEnv,
) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = ["401"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert (sendenv.row(job_id).state, sendenv.row(job_id).error_category) == (
        "failed",
        "not_connected",
    )
    assert sendenv.status(planted.owner) == "needs_reconnect"
    # A later press is refused before anything is sent.
    again = sendenv.job(planted)
    await sendenv.run_next()
    assert (sendenv.row(again).state, sendenv.row(again).error_category) == (
        "failed",
        "not_connected",
    )
    assert len(sendenv.fake.posts) == 1


async def test_a_request_that_never_left_is_retried_with_the_same_number_and_key(
    sendenv: SendEnv,
) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = ["refuse"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "queued"  # retryable: back in the queue
    [op] = sendenv.operations(planted.owner)
    assert (op.state, op.error_category) == ("failed", "upstream_unavailable")
    assert sendenv.fake.gets == []  # never left: nothing to reconcile
    sendenv.ready(job_id)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    first, again = sendenv.fake.posts
    assert first == again
    [op] = sendenv.operations(planted.owner)  # the failed row was taken over, not duplicated
    assert (op.state, op.idempotency_key) == ("succeeded", operation_key(planted.class_id, 1))
    assert last_number(sendenv, planted) == 1


async def test_uncertain_and_reconciled_equal_succeeds(sendenv: SendEnv) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = ["drop"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    assert sendenv.fake.gets == [str(planted.class_id)]
    [op] = sendenv.operations(planted.owner)
    assert (op.state, op.resolved_by, op.external_id) == ("succeeded", "worker", "1")
    assert sendenv.publication(planted.owner, planted.class_id).sent_revision_id == (
        planted.revision_id
    )


async def test_uncertain_and_reconciled_lower_is_retried_with_the_same_number(
    sendenv: SendEnv,
) -> None:
    planted, _ = await connected(sendenv)
    sendenv.job(planted)
    await sendenv.run_next()  # the player holds 1
    sendenv.fake.script = ["lost"]  # number 2 never applied, its answer lost
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "queued"  # retryable: back in the queue
    ops = sendenv.operations(planted.owner)
    assert (ops[-1].state, ops[-1].idempotency_key) == (
        "failed", operation_key(planted.class_id, 2),
    )  # fmt: skip
    sendenv.ready(job_id)
    await sendenv.run_next()
    assert sendenv.row(job_id).state == "completed"
    assert [p["publication_number"] for p in sendenv.fake.posts] == [1, 2, 2]
    assert held(sendenv, planted) == 2
    assert last_number(sendenv, planted) == 2


async def test_uncertain_with_no_answer_stays_uncertain_and_blocks_the_next_press(
    sendenv: SendEnv,
) -> None:
    planted, _ = await connected(sendenv)
    sendenv.fake.script = ["drop"]
    sendenv.fake.reads = ["none"]
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    row = sendenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [op] = sendenv.operations(planted.owner)
    assert op.state == "uncertain"
    again = sendenv.job(planted)
    await sendenv.run_next()
    assert (sendenv.row(again).state, sendenv.row(again).error_category) == (
        "failed", "uncertain_write",
    )  # fmt: skip
    assert len(sendenv.fake.posts) == 1  # never sent again automatically


async def test_a_disconnected_player_sends_nothing(sendenv: SendEnv) -> None:
    planted, _ = await connected(sendenv)
    sendenv.set_status(planted.owner, "needs_reconnect")
    job_id = sendenv.job(planted)
    await sendenv.run_next()
    assert (sendenv.row(job_id).state, sendenv.row(job_id).error_category) == (
        "failed",
        "not_connected",
    )
    assert sendenv.fake.posts == []


def test_the_operation_key_names_the_class_and_number() -> None:
    class_id = UUID(int=7)
    assert operation_key(class_id, 3) == f"player:{class_id}:3"
