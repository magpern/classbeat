"""The update as an external operation (ADR 0007 sections 7 and 8, issue SV2): failure injection at
every request of the re-save, MOWL making a copy, a partial update repaired by Do it anyway and an
update with ``overwrite``, and a crash mid-call. The in-process fake MOWL (``savetools``); never
the network.
"""

import asyncio
import threading
from typing import Any
from uuid import UUID

import pytest

from classbeat import mowl_state
from classbeat.jobs import queue
from classbeat.saving import operation
from optools import TOKEN_A
from savetools import LINKED_PLAYLIST, SEGMENTS, WRITES, FakeMowlPrograms, spec_of
from updatetools import DRAFT_2, DRAFT_RENAMED, Live, UpdateEnv

OPS = "/api/external-operations"
WAIT = operation.bound_seconds(SEGMENTS) + 30  # actionable_at - pending_at


def program_matches(fake: FakeMowlPrograms, live: Live, content: dict[str, Any]) -> bool:
    read = fake.read_program(fake.programs[live.program_id])
    return mowl_state.matches(spec_of(content), read, playlist_id=LINKED_PLAYLIST)


@pytest.mark.parametrize("mode", ["refuse", "drop"])
@pytest.mark.parametrize("number", range(1, WRITES + 1))
async def test_a_failure_that_left_the_program_unchanged_is_failed_and_retried_once(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, mode: str, number: int
) -> None:
    """The header of DRAFT_2 is the saved one's, and new segment copies are not part of the
    program until its segment list is replaced (the last request): a failure anywhere before
    that leaves the program equal to ``before_hash`` with no new program, so the reconciliation
    proves no effect, the job retries once (from ``prepare``) and succeeds. A lost answer to the
    last request is the update itself: the reconciliation finds it."""
    live = await uenv.live()
    revision = uenv.revision(live)
    start = fake_programs.write_count
    fake_programs.fail = {start + number: mode}
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    states = uenv.op_states(job_id)
    assert states[:2] == ["pending", "uncertain"]  # the write itself is never "failed"
    assert fake_programs.write_count - start == number  # the library stopped at the failure
    if mode == "drop" and number == WRITES:
        assert states == ["pending", "uncertain", "succeeded"]
        assert uenv.row(job_id).state == "completed"
        assert uenv.stages(job_id) == ["prepare", "read", "save", "verify"]
    else:
        assert states == ["pending", "uncertain", "failed"]  # by proof
        [op] = uenv.operations(live.saved.owner)
        assert (op.state, op.resolved_by) == ("failed", "worker")
        assert uenv.row(job_id).state == "queued"  # retried once
        assert not program_matches(fake_programs, live, DRAFT_2)
        uenv.ready(job_id)
        await uenv.run_next()
        assert uenv.row(job_id).state == "completed"
        stages = ["prepare", "read", "save", "prepare", "read", "save", "verify"]
        assert uenv.stages(job_id) == stages
        assert uenv.row(job_id).attempt == 2
    assert program_matches(fake_programs, live, DRAFT_2)
    assert len(fake_programs.owned(TOKEN_A)) == 1  # updated in place, nothing created
    row = uenv.saved_row(live.saved_workout_id)
    assert (row.revision_id, row.program_id) == (revision, live.program_id)
    assert row.state_hash is not None


@pytest.mark.parametrize("mode", ["refuse", "drop"])
@pytest.mark.parametrize("number", range(1, WRITES + 1))
async def test_a_partial_update_stays_uncertain_and_is_repaired_by_an_overwrite(
    uenv: UpdateEnv,
    fake_programs: FakeMowlPrograms,
    update_browser: Any,
    make_actor: Any,
    mode: str,
    number: int,
) -> None:
    """DRAFT_RENAMED changes the header: once the header request was applied and the segment
    list was not, the program is neither the old state nor the new (partial): ``uncertain``,
    never retried. Do it anyway, then an update with ``overwrite``, repairs it. A refused first
    request applied nothing (``failed``, retried); a lost last answer is the update itself."""
    live = await uenv.live()
    browser = await update_browser(live)
    revision = uenv.revision(live, DRAFT_RENAMED)
    start = fake_programs.write_count
    fake_programs.fail = {start + number: mode}
    first = await browser.update(revision, DRAFT_RENAMED)
    assert first.status_code == 201
    job_id = UUID(first.json()["id"])
    await uenv.run_next()
    states = uenv.op_states(job_id)
    if (mode, number) == ("refuse", 1):
        assert states == ["pending", "uncertain", "failed"]
        assert uenv.row(job_id).state == "queued"
        return
    if (mode, number) == ("drop", WRITES):
        assert states == ["pending", "uncertain", "succeeded"]
        assert uenv.row(job_id).state == "completed"
        return
    assert states == ["pending", "uncertain"]
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")  # not retried
    [op] = uenv.operations(live.saved.owner)
    assert op.state == "uncertain"
    assert fake_programs.programs[live.program_id].body["Name"] == DRAFT_RENAMED["name"]
    assert not program_matches(fake_programs, live, DRAFT_RENAMED)
    saved_row = uenv.saved_row(live.saved_workout_id)
    assert saved_row.revision_id == live.saved.revision_id  # not moved

    actor = await make_actor(live.saved.owner)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert (answer.status_code, answer.json()["state"]) == (200, "uncertain")
    answer = await actor.post(f"{OPS}/{op.id}/do-it-anyway")
    assert (answer.status_code, answer.json()["state"]) == (200, "failed")

    # the plain update now meets the partial state: changed, nothing written
    writes = fake_programs.write_count
    plain = await browser.update(revision, DRAFT_RENAMED)
    assert plain.status_code == 201, plain.text
    await uenv.run_next()
    assert uenv.row(UUID(plain.json()["id"])).error_category == "program_changed"
    assert fake_programs.write_count == writes
    # "Update anyway (replaces your changes in MOWL)"
    fake_programs.fail = {}
    repair = await browser.update(revision, DRAFT_RENAMED, overwrite=True)
    assert repair.status_code == 201, repair.text
    await uenv.run_next()
    assert uenv.row(UUID(repair.json()["id"])).state == "completed"
    assert program_matches(fake_programs, live, DRAFT_RENAMED)
    assert len(fake_programs.owned(TOKEN_A)) == 1
    saved_row = uenv.saved_row(live.saved_workout_id)
    assert (saved_row.revision_id, saved_row.name) == (revision, DRAFT_RENAMED["name"])
    assert saved_row.state_hash is not None


async def test_a_copy_made_by_mowl_is_uncertain_never_retried_and_leaves_one_stray(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any, make_actor: Any
) -> None:
    """MOWL answers the update with another program id: a new, empty program; the target is
    untouched and still equals ``before_hash``, but the listing shows the new program, so the
    reconciliation is inconclusive. The job is not retried (``max_attempts`` 3), Check again
    stays inconclusive, and after the user's repair there is exactly one stray program."""
    live = await uenv.live()
    browser = await update_browser(live)
    revision = uenv.revision(live)
    start = fake_programs.write_count
    fake_programs.fail = {start + 1: "copy"}
    answer = await browser.update(revision, DRAFT_2)
    job_id = UUID(answer.json()["id"])
    assert uenv.row(job_id).max_attempts == 3
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    assert uenv.op_states(job_id) == ["pending", "uncertain"]  # never succeeded, never failed
    assert fake_programs.write_count - start == 1  # the library stopped at the copy
    [op] = uenv.operations(live.saved.owner)
    assert op.state == "uncertain"
    owned = fake_programs.owned(TOKEN_A)
    assert len(owned) == 2
    [stray] = [p for p in owned if p.id != live.program_id]
    assert stray.segment_ids == []
    # the target still equals before_hash
    target = fake_programs.read_program(fake_programs.programs[live.program_id])
    before = op.request_context["before_hash"]
    assert mowl_state.state_hash(mowl_state.normalized_program(target)).hex() == before

    # the job is over: nothing claims it again, nothing more is sent
    lease = uenv.s.ops.app.settings.job_lease_seconds
    assert await queue.claim(uenv.s.ops.factory, "w", lease, ["update_workout"]) is None
    actor = await make_actor(live.saved.owner)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert (answer.status_code, answer.json()["state"]) == (200, "uncertain")
    assert fake_programs.write_count - start == 1

    answer = await actor.post(f"{OPS}/{op.id}/do-it-anyway")
    assert answer.json()["state"] == "failed"
    repair = await browser.update(revision, DRAFT_2, overwrite=True)
    assert repair.status_code == 201, repair.text
    await uenv.run_next()
    assert uenv.row(UUID(repair.json()["id"])).state == "completed"
    assert program_matches(fake_programs, live, DRAFT_2)
    strays = [p for p in fake_programs.owned(TOKEN_A) if p.id != live.program_id]
    assert [p.id for p in strays] == [stray.id]  # exactly one stray after the whole flow


async def test_an_answer_naming_another_program_is_never_this_updates_success(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if the library returned (it raises first), an answer for another program is never
    recorded as this update's success: ``uncertain``, then the reconciliation reads the target
    (unchanged here, no new program: ``failed`` by proof)."""
    from classbeat.saving import update as update_kind

    live = await uenv.live()

    def other_program(*args: Any) -> Any:
        return {"program_id": live.program_id + 1, "segment_ids": [], "updated": True}

    monkeypatch.setattr(update_kind, "write_program", other_program)
    job_id = uenv.job(live, uenv.revision(live), DRAFT_2)
    await uenv.run_next()
    assert uenv.op_states(job_id) == ["pending", "uncertain", "failed"]
    [op] = uenv.operations(live.saved.owner)
    assert (op.state, op.external_id) == ("failed", None)
    assert uenv.saved_row(live.saved_workout_id).revision_id == live.saved.revision_id


async def hold_mid_call(uenv: UpdateEnv, number: int) -> asyncio.Task[None]:
    fake = uenv.fake
    fake.hold_at, fake.held, fake.release = number, threading.Event(), threading.Event()
    execution = await uenv.s.claim()
    task = asyncio.ensure_future(uenv.s.execute(execution))
    assert await asyncio.to_thread(fake.held.wait, 10), "the write never reached the fake"
    return task


async def test_after_a_crash_the_next_attempt_never_calls_and_check_again_resolves(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, make_actor: Any
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    start = fake_programs.write_count
    job_id = uenv.job(live, revision, DRAFT_2)
    task = await hold_mid_call(uenv, start + 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fake_programs.release.set()
    for _ in range(500):  # the thread finishes the update in MOWL
        if fake_programs.write_count - start >= WRITES:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    uenv.s.lose_lease(job_id)
    assert await queue.reap(uenv.s.ops.factory) == 1
    [op] = uenv.operations(live.saved.owner)
    assert (op.state, op.call_ended_at) == ("pending", None)
    writes = fake_programs.write_count

    uenv.ready(job_id)
    await uenv.run_next()  # restarts at prepare
    row = uenv.row(job_id)
    # the read differs (the crashed write landed), but the job's own pending row decides:
    # never called again, never reported as the user's edit
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    assert uenv.stages(job_id) == ["prepare", "read", "save"] * 2
    assert fake_programs.write_count == writes

    actor = await make_actor(live.saved.owner)
    [op] = uenv.operations(live.saved.owner)
    assert op.state == "uncertain"  # the orphan was marked
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert answer.json() == {"detail": {"reason": "call_may_be_running"}}
    uenv.s.age(op.id, WAIT + 1)
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert (answer.status_code, answer.json()["state"]) == (200, "succeeded")
    saved_row = uenv.saved_row(live.saved_workout_id)
    assert (saved_row.revision_id, saved_row.state_hash) == (revision, None)  # not verified
    assert fake_programs.write_count == writes


async def test_a_deferred_refusal_holds_when_the_own_row_turned_failed_before_the_write(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read refused, but the job's own crashed attempt left a ``pending`` row, so the
    refusal was deferred to the guard. If that row is ``failed`` by the time of the decide
    transaction (Do it anyway on it meanwhile), ``perform`` would take it over and call: the
    guard refuses with the deferred category instead, before anything is sent."""
    from classbeat.saving import update as update_kind

    live = await uenv.live()
    revision = uenv.revision(live)
    start = fake_programs.write_count
    job_id = uenv.job(live, revision, DRAFT_2)
    task = await hold_mid_call(uenv, start + WRITES)  # everything but the segment list
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fake_programs.fail = {start + WRITES: "refuse"}
    fake_programs.release.set()
    await asyncio.sleep(0.1)
    fake_programs.edit(live.program_id, Name="Edited by the user")  # the read will refuse
    uenv.s.lose_lease(job_id)
    assert await queue.reap(uenv.s.ops.factory) == 1
    real = update_kind.perform

    async def failed_then_perform(run: Any, request: Any, call: Any, **kwargs: Any) -> Any:
        uenv.sql(
            "UPDATE external_operations SET state = 'failed', resolved_by = 'user' "
            "WHERE kind = 'mowl_update_workout' AND job_id = :j",
            j=job_id,
        )
        return await real(run, request, call, **kwargs)

    monkeypatch.setattr(update_kind, "perform", failed_then_perform)
    writes = fake_programs.write_count
    uenv.ready(job_id)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "program_changed")
    assert fake_programs.write_count == writes  # never called
    [op] = uenv.operations(live.saved.owner)
    assert op.state == "failed"
