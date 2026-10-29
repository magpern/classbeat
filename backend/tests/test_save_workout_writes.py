"""The save as an external operation (ADR 0007 sections 6, 8 and 9, issue SV1): failure injection
at every request of the write, a crash mid-call, orphaned rows and the unresolved-write rule, a
reconnect mid-call and the take-over of a ``failed`` row. The in-process fake MOWL
(``savetools``); never the network.
"""

import asyncio
import threading
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from classbeat import saving
from classbeat.jobs import queue
from classbeat.operations import UncertainAnswer, user
from classbeat.saving import kind as save_kind
from classbeat.saving import operation
from ctx import make_context
from optools import TOKEN_A, TOKEN_B
from savetools import SEGMENTS, WRITES, FakeMowlPrograms, SaveEnv

OPS = "/api/external-operations"
WAIT = operation.bound_seconds(SEGMENTS) + 30  # actionable_at - pending_at


@pytest.mark.parametrize("mode", ["refuse", "drop"])
@pytest.mark.parametrize("number", range(1, WRITES + 1))
async def test_a_failure_at_any_request_is_uncertain_never_failed(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, mode: str, number: int
) -> None:
    saved = await senv.saved_class()
    fake_programs.fail = {number: mode}
    job_id = senv.job(saved)
    await senv.run_next()
    states = senv.op_states(job_id)
    assert states[:2] == ["pending", "uncertain"]  # the write itself is never "failed"
    assert "failed" not in states
    assert fake_programs.write_count == number  # the library stopped at the failure
    [op] = senv.operations(saved.owner)
    row = senv.row(job_id)
    if mode == "drop" and number == WRITES:
        # MOWL applied every request; only the last answer was lost: the reconciliation finds
        # exactly one new matching program.
        assert states == ["pending", "uncertain", "succeeded"]
        assert (op.state, op.resolved_by, row.state) == ("succeeded", "worker", "completed")
        [program] = fake_programs.owned(TOKEN_A)
        assert op.external_id == str(program.id)
        [saved_row] = senv.saved_rows(saved.owner)
        assert saved_row.program_id == program.id
        assert saved_row.operation_id == op.id
    else:
        assert (op.state, row.state, row.error_category) == (
            "uncertain", "failed", "uncertain_write",
        )  # fmt: skip
        assert senv.saved_rows(saved.owner) == []
        # created (refused at 1: nothing; otherwise a partial program the user can delete)
        assert len(fake_programs.owned(TOKEN_A)) == (0 if (mode, number) == ("refuse", 1) else 1)


@pytest.mark.parametrize("number", [1, 2])  # the program's PUT, the first segment's PUT
async def test_an_answer_without_its_id_is_uncertain(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, number: int
) -> None:
    saved = await senv.saved_class()
    fake_programs.fail = {number: "noid"}
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.op_states(job_id) == ["pending", "uncertain"]
    assert (senv.row(job_id).error_category, fake_programs.write_count) == (
        "uncertain_write", number,
    )  # fmt: skip


def test_the_call_answer_needs_a_whole_program_id() -> None:
    read = save_kind._program_id  # pyright: ignore[reportPrivateUsage]
    assert read({"program_id": 7, "segment_ids": [1]}) == 7
    for answer in (
        {},
        {"program_id": "7"},
        {"program_id": True},
        {"program_id": 0},
        {"program_id": 2**63},
        {"program_id": 7.0},
        [7],
        None,
    ):
        with pytest.raises(UncertainAnswer):
            read(answer)


async def hold_mid_call(senv: SaveEnv, number: int) -> asyncio.Task[None]:
    """Run the next job until the fake holds write request ``number`` (not yet applied)."""
    fake = senv.fake
    fake.hold_at, fake.held, fake.release = number, threading.Event(), threading.Event()
    execution = await senv.claim()
    task = asyncio.ensure_future(senv.execute(execution))
    assert await asyncio.to_thread(fake.held.wait, 10), "the write never reached the fake"
    return task


async def until_writes(fake: FakeMowlPrograms, count: int) -> None:
    for _ in range(500):
        if fake.write_count >= count:
            await asyncio.sleep(0.05)  # the last answer goes back
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the library thread never finished")


async def crash_mid_call(senv: SaveEnv, job_id: UUID, number: int) -> None:
    """The worker dies while write ``number`` is in flight; MOWL then finishes what the thread
    still sends (the whole save); the lease runs out and the reaper ends or requeues the job."""
    task = await hold_mid_call(senv, number)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    senv.fake.release.set()
    await until_writes(senv.fake, WRITES)
    senv.lose_lease(job_id)
    assert await queue.reap(senv.ops.factory) == 1


async def test_after_a_crash_the_next_attempt_never_calls_and_check_again_finds_the_save(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, make_actor: Any
) -> None:
    saved = await senv.saved_class()
    job_id = senv.job(saved)
    await crash_mid_call(senv, job_id, 4)
    [op] = senv.operations(saved.owner)
    assert (op.state, op.call_ended_at) == ("pending", None)
    writes = fake_programs.write_count

    senv.ready(job_id)
    await senv.run_next()  # restarts at prepare; meets its stale pending row: never calls again
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    stages = [p["stage"] for _, k, p, _ in senv.ops.events(job_id) if k == "stage_started"]
    assert stages == ["prepare", "save", "prepare", "save"]
    [op] = senv.operations(saved.owner)
    assert (op.state, op.call_ended_at) == ("uncertain", None)
    assert fake_programs.write_count == writes
    assert fake_programs.gets == 0  # not reconciled before actionable_at

    actor = await make_actor(saved.owner)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert (answer.status_code, answer.json()) == (
        409,
        {"detail": {"reason": "call_may_be_running"}},
    )
    senv.age(op.id, WAIT + 1)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")  # the user's Check again
    assert answer.status_code == 200, answer.text
    [op] = senv.operations(saved.owner)
    [program] = fake_programs.owned(TOKEN_A)
    assert (op.state, op.resolved_by, op.external_id) == ("succeeded", "user", str(program.id))
    [saved_row] = senv.saved_rows(saved.owner)
    assert (saved_row.program_id, saved_row.operation_id, saved_row.class_id) == (
        program.id, op.id, saved.class_id,
    )  # fmt: skip
    assert fake_programs.write_count == writes  # reads only


async def test_an_orphan_of_a_reaped_job_blocks_saves_until_the_user_resolves_it(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any, make_actor: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    job_id = senv.job(saved, columns={"max_attempts": 1})
    fake_programs.fail = {WRITES: "refuse"}  # the thread stops before the segment list: partial
    await crash_mid_call(senv, job_id, 3)
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "lease_lost")  # the last attempt
    [op] = senv.operations(saved.owner)
    assert op.state == "pending"  # nobody will ever record its outcome
    writes = fake_programs.write_count

    refused = await browser.save()  # creation: the orphan is another job's unresolved write
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "write_unresolved")

    other = senv.job(saved)  # a job that got past creation: its prepare refuses too
    await senv.run_next()
    assert (senv.row(other).state, senv.row(other).error_category) == ("failed", "uncertain_write")
    assert fake_programs.write_count == writes  # nothing sent by either
    [op] = senv.operations(saved.owner)
    assert (op.state, op.error_category) == ("uncertain", "lease_lost")  # resolve marked it

    actor = await make_actor(saved.owner)
    senv.age(op.id, WAIT + 1)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert answer.status_code == 200
    assert answer.json()["state"] == "uncertain"
    still = await browser.save()
    assert (still.status_code, still.json()["detail"]["reason"]) == (409, "write_unresolved")
    answer = await actor.post(f"{OPS}/{op.id}/do-it-anyway")
    assert answer.status_code == 200
    assert answer.json()["state"] == "failed"

    fake_programs.fail = {}
    accepted = await browser.save()
    assert accepted.status_code == 201, accepted.text
    await senv.run_next()
    assert senv.row(UUID(accepted.json()["id"])).state == "completed"
    assert len(fake_programs.owned(TOKEN_A)) == 2  # the partial one and the new one
    assert len(senv.saved_rows(saved.owner)) == 1


async def test_an_uncertain_save_refuses_the_next_until_check_again_resolves_it(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any, make_actor: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    fake_programs.fail = {WRITES: "drop"}  # applied in full, the answer lost
    first = await browser.save()
    execution = await senv.claim()
    # The preflight reads the listing once; make the reconciliation's listing fail.
    real_listing = fake_programs._listing  # pyright: ignore[reportPrivateUsage]
    calls: list[int] = []

    def listing(req: Any) -> Any:
        calls.append(1)
        if len(calls) > 1:
            fake_programs.list_status = 503
        return real_listing(req)

    fake_programs._listing = listing  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    await senv.execute(execution)
    assert senv.row(UUID(first.json()["id"])).error_category == "uncertain_write"
    [op] = senv.operations(saved.owner)
    assert op.state == "uncertain"
    refused = await browser.save()
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "write_unresolved")

    fake_programs._listing = real_listing  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    fake_programs.list_status = None
    actor = await make_actor(saved.owner)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert answer.status_code == 200
    assert answer.json()["state"] == "succeeded"
    assert len(senv.saved_rows(saved.owner)) == 1
    accepted = await browser.save()
    assert accepted.status_code == 201


async def test_two_jobs_on_one_class_never_both_call(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    """While a save is in flight no second job of the class can exist (one open job per class,
    the API's ``class_busy`` and the index behind it); once it ended, its rows decide."""
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    first = await browser.save()
    task = await hold_mid_call(senv, 2)
    busy = await browser.save()
    assert (busy.status_code, busy.json()["detail"]["reason"]) == (409, "class_busy")
    with pytest.raises(IntegrityError, match="jobs_one_open_per_class"):
        senv.job(saved)
    fake_programs.release.set()
    await asyncio.wait_for(task, 15)
    assert senv.row(UUID(first.json()["id"])).state == "completed"
    assert fake_programs.write_count == WRITES  # the first job's writes only
    assert len(fake_programs.owned(TOKEN_A)) == 1


async def test_a_reconnect_mid_call_ends_not_connected_with_no_row_left(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    job_id = senv.job(saved)
    task = await hold_mid_call(senv, 4)
    await senv.ops.connect(saved.owner, TOKEN_B)  # an account switch while the call runs
    assert senv.operations(saved.owner) == []  # the reconnect deleted the operation row
    fake_programs.release.set()
    await asyncio.wait_for(task, 15)
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert senv.operations(saved.owner) == []
    assert senv.saved_rows(saved.owner) == []
    # the call already made went to the account whose token it opened, and nowhere else
    assert set(fake_programs.write_tokens) == {TOKEN_A}
    assert fake_programs.owned(TOKEN_B) == []
    links = senv.sql("SELECT count(*) FROM mowl_playlist_links WHERE owner_id = :o", o=saved.owner)
    assert links.scalar_one() == 0
    # not retried: the revision's music is not in the new account
    assert senv.row(job_id).attempt == 1


async def test_a_take_over_of_a_failed_row_resets_the_attempt_and_honours_the_bound(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, make_actor: Any
) -> None:
    saved = await senv.saved_class()
    saving.configure_listing_lag(0.001)
    fake_programs.fail = {1: "refuse"}  # nothing created: a covered listing proves no effect
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.op_states(job_id) == ["pending", "uncertain", "failed"]
    [failed] = senv.operations(saved.owner)
    assert (failed.state, failed.resolved_by) == ("failed", "worker")
    assert failed.call_ended_at is not None
    assert failed.request_context["baseline"] == []
    assert senv.row(job_id).state == "queued"  # retried: no effect was proved

    planted = fake_programs.plant(TOKEN_A, name="Made meanwhile")  # the new baseline differs
    senv.ready(job_id)
    await crash_mid_call(senv, job_id, 2)  # the take-over's call, interrupted
    [taken] = senv.operations(saved.owner)
    assert taken.id == failed.id
    assert taken.state == "pending"
    assert taken.pending_at > failed.pending_at
    assert taken.call_ended_at is None
    assert taken.request_context["baseline"] == [planted]
    assert (taken.resolved_by, taken.error_category, taken.external_id) == (None, None, None)

    senv.ready(job_id)
    await senv.run_next()  # the stale call may still run: no reconciliation, no call
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [op] = senv.operations(saved.owner)
    assert (op.state, op.call_ended_at) == ("uncertain", None)
    view = await user.get(senv.ops.factory, make_context(saved.owner), op.id)
    assert view is not None
    assert view.actionable_at is not None
    assert (view.actionable_at - op.pending_at).total_seconds() == pytest.approx(WAIT)
    actor = await make_actor(saved.owner)
    for action in ("check-again", "do-it-anyway"):
        answer = await actor.post(f"{OPS}/{op.id}/{action}")
        assert answer.json() == {"detail": {"reason": "call_may_be_running"}}
    senv.age(op.id, WAIT + 1)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert answer.json()["state"] == "succeeded"  # the interrupted call completed in MOWL


@pytest.mark.parametrize("change", ["reconnect", "link"])
async def test_the_decide_guard_refuses_a_change_after_prepare_before_any_row(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Section 5.3: between ``prepare`` and the write the account was switched, or the link now
    names another playlist: ``not_connected`` before ``pending`` is written, nothing sent."""
    saved = await senv.saved_class()
    real = save_kind.perform

    async def changed_then_perform(run: Any, request: Any, call: Any, **kwargs: Any) -> Any:
        if change == "reconnect":
            await senv.ops.connect(saved.owner, TOKEN_A)  # a reconnect in place: a new version
        else:
            senv.sql(
                "UPDATE mowl_playlist_links SET playlist_id = 999 WHERE id = :id", id=saved.link_id
            )
        return await real(run, request, call, **kwargs)

    monkeypatch.setattr(save_kind, "perform", changed_then_perform)
    job_id = senv.job(saved)
    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert senv.operations(saved.owner) == []
    assert senv.op_states(job_id) == []
    assert fake_programs.writes == []
    assert fake_programs.lists == 1
