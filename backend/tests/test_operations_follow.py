"""An uncertain attach row follows the job that fails on it (#125, ADR 0005 section 7).

A later job that turns a stale ``pending`` row ``uncertain``, or meets a row that is
``uncertain`` already, sets the row's ``job_id`` to itself in the same fenced transaction, so the
listing of the job that failed ``uncertain_write`` shows the decision to make. The older job's
listing no longer shows it (the row is still found by its id). ``job_id`` of an ``uncertain`` row
is no holder: only a ``pending`` row's ``(job_id, lease_version)`` is. Fake MOWL only."""

import asyncio
import contextlib
import threading
import uuid
from typing import Any

from classbeat.jobs import queue
from optools import PLAYLIST, TOKEN_A, FakeMowlAttach, OpsEnv, wait_entered
from test_operations import owner_with_job

OPS = "/api/external-operations"


async def listed(actor: Any, job_id: uuid.UUID) -> list[str]:
    response = await actor.get(f"{OPS}?job_id={job_id}")
    assert response.status_code == 200
    return [o["id"] for o in response.json()["operations"]]


async def test_a_later_job_failing_on_an_uncertain_row_lists_it_and_can_decide(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner, connection, first = await owner_with_job(xenv)
    fake_attach.mode = "timeout_after_accept"
    await xenv.run_next()
    fake_attach.mode = "ok"
    [op] = xenv.operations(owner)
    assert (op.state, op.job_id) == ("uncertain", first)
    actor = await make_actor(owner)
    assert await listed(actor, first) == [str(op.id)]

    second = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(second).error_category == "uncertain_write"
    [op] = xenv.operations(owner)
    assert (op.state, op.job_id, op.lease_version) == ("uncertain", second, 1)  # call's token
    assert await listed(actor, second) == [str(op.id)]
    assert await listed(actor, first) == []  # the older job's page: it moved on
    assert (await actor.get(f"{OPS}/{op.id}")).json()["job_id"] == str(second)
    events = [p for _, k, p, _ in xenv.events(second) if k == "external_operation"]
    assert events == [{"kind": "mowl_attach_spotify_playlist", "state": "uncertain"}]
    assert len(fake_attach.attaches) == 1

    # From the new job's page: Check again (inconclusive), then Do it anyway.
    checked = await actor.post(f"{OPS}/{op.id}/check-again")
    assert (checked.status_code, checked.json()["state"]) == (200, "uncertain")
    decided = await actor.post(f"{OPS}/{op.id}/do-it-anyway")
    assert (decided.status_code, decided.json()["state"]) == (200, "failed")
    third = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(third).state == "completed"
    [op] = xenv.operations(owner)
    assert (op.state, op.job_id, op.resolved_by) == ("succeeded", third, "worker")
    assert len(fake_attach.attaches) == 2
    # Another user never sees it, by job or by id.
    stranger = await make_actor(await xenv.user())
    assert await listed(stranger, second) == []
    assert (await stranger.get(f"{OPS}/{op.id}")).status_code == 404


async def test_after_a_reaped_holder_each_later_job_lists_the_row_it_failed_on(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner, connection, first = await owner_with_job(xenv)
    xenv.sql("UPDATE jobs SET max_attempts = 1 WHERE id = :id", id=first)
    fake_attach.gate = threading.Event()
    task = asyncio.ensure_future(xenv.execute(await xenv.claim()))
    await wait_entered(fake_attach)
    task.cancel()  # the worker dies mid-attach
    with contextlib.suppress(asyncio.CancelledError):
        await task
    fake_attach.gate.set()
    assert await asyncio.to_thread(fake_attach.finished.wait, 10)
    xenv.sql(
        "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id", id=first
    )
    assert await queue.reap(xenv.factory) == 1
    assert (xenv.row(first).state, xenv.row(first).error_category) == ("failed", "lease_lost")
    [op] = xenv.operations(owner)
    assert (op.state, op.job_id) == ("pending", first)

    actor = await make_actor(owner)
    previous = first
    for _ in range(2):
        later = xenv.job(owner, connection)
        await xenv.run_next()
        assert xenv.row(later).error_category == "uncertain_write"
        [op] = xenv.operations(owner)
        assert (op.state, op.job_id) == ("uncertain", later)
        assert await listed(actor, later) == [str(op.id)]
        assert await listed(actor, previous) == []
        previous = later
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]  # never re-sent
