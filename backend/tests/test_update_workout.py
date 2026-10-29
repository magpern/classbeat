"""``update_workout`` (ADR 0007 sections 5, 7 and 9, issue SV2): the happy path through the jobs
API and the worker, which program may be written (only the user's own live saved row of the
class, never a program id from the input), read first (``program_changed``,
``program_missing``, ``overwrite``), reconnect, the unresolved-write rule and the request hash.
Every refusal is counted at the fake: nothing sent. The in-process fake MOWL (``savetools``);
never the network.
"""

import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from classbeat import mowl_state, saving
from classbeat.jobs import KINDS
from classbeat.saving import operation, update_operation
from classbeat.saving import update as update_kind
from ctx import make_context
from jobtools import new_class, new_job
from optools import TOKEN_A, TOKEN_B
from savetools import DRAFT, LINKED_PLAYLIST, SEGMENTS, WRITES, FakeMowlPrograms, spec_of
from updatetools import DRAFT_2, DRAFT_RENAMED, Live, UpdateEnv, draft_hash


def test_the_kind_is_registered_without_credit_and_with_admit() -> None:
    kind = KINDS.get(saving.UPDATE_KIND)
    assert kind is saving.UPDATE_WORKOUT
    assert kind is not None
    assert (kind.consumes_credit, kind.stages) == (False, ("prepare", "read", "save", "verify"))
    assert kind.admit is update_kind.admit
    assert kind.server_fields == frozenset()


def test_the_input_names_ids_a_hash_and_the_confirmation_only() -> None:
    good = {
        "class_id": str(UUID(int=1)),
        "revision_id": str(UUID(int=2)),
        "content_hash": "ab" * 32,
        "saved_workout_id": str(UUID(int=4)),
        "mowl_connection_id": str(UUID(int=3)),
    }
    parsed = saving.UpdateWorkoutInput.model_validate(good)
    assert parsed.overwrite is False  # never inferred: only the page's confirmation sets it
    assert saving.UpdateWorkoutInput.model_validate({**good, "overwrite": True}).overwrite
    for bad in (
        {**good, "program_id": 5},  # a program id is never taken from the browser
        {**good, "ProgramID": 5},
        {**good, "playlist_id": 5},
        {**good, "name": "x"},
        {**good, "overwrite": "true"},  # strict: no coercion
        {**good, "overwrite": 1},
        {**good, "content_hash": "AB" * 32},
        {k: v for k, v in good.items() if k != "saved_workout_id"},
        {k: v for k, v in good.items() if k != "class_id"} | {"title": "New"},
    ):
        with pytest.raises(ValidationError):
            saving.UpdateWorkoutInput.model_validate(bad)


def test_the_request_hash_covers_program_content_and_playlist() -> None:
    base = update_operation.update_hash(700, "ab" * 32, 1001)
    assert base == update_operation.update_hash(700, "ab" * 32, 1001)
    assert base != update_operation.update_hash(701, "ab" * 32, 1001)
    assert base != update_operation.update_hash(700, "cd" * 32, 1001)
    assert base != update_operation.update_hash(700, "ab" * 32, 1002)
    assert base != operation.save_hash("ab" * 32, 1001)
    assert update_operation.idempotency_key(UUID(int=9)) == f"{UUID(int=9)}:update"


async def test_happy_path_updates_the_program_in_place_and_moves_the_saved_row(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    browser = await update_browser(live)
    before_writes = fake_programs.write_count
    answer = await browser.update(revision, DRAFT_2)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    row = uenv.row(job_id)
    assert (row.kind, row.credit_state, row.class_id) == (
        "update_workout", "none", live.saved.class_id,
    )  # fmt: skip

    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert uenv.stages(job_id) == ["prepare", "read", "save", "verify"]
    assert uenv.op_states(job_id) == ["pending", "succeeded"]

    [program] = fake_programs.owned(TOKEN_A)  # updated in place: still one program
    assert program.id == live.program_id
    read = fake_programs.read_program(program)
    assert mowl_state.matches(spec_of(DRAFT_2), read, playlist_id=LINKED_PLAYLIST)
    assert read["IsPublic"] is False
    writes = fake_programs.writes[-WRITES:]
    assert fake_programs.write_count - before_writes == WRITES
    assert writes[0].endswith(f"PUT /v1/programs/{live.program_id}")
    assert writes[-1].endswith(f"PUT /v1/programs/{live.program_id}/segments")

    [op] = uenv.operations(live.saved.owner)
    hash_hex = draft_hash(DRAFT_2).hex()
    assert (op.state, op.external_id, op.class_id, op.job_id) == (
        "succeeded", str(live.program_id), live.saved.class_id, job_id,
    )  # fmt: skip
    assert op.idempotency_key == f"{job_id}:update"
    assert bytes(op.request_hash) == update_operation.update_hash(
        live.program_id, hash_hex, LINKED_PLAYLIST
    )
    before = uenv.saved_row(live.saved_workout_id)
    assert op.request_context == {
        "saved_workout_id": str(live.saved_workout_id),
        "revision_id": str(revision),
        "content_hash": hash_hex,
        "playlist_id": LINKED_PLAYLIST,
        "program_id": live.program_id,
        "before_hash": mowl_state.state_hash(
            mowl_state.normalized_spec(spec_of(DRAFT), LINKED_PLAYLIST)
        ).hex(),
        "baseline": [live.program_id],
        "bound_seconds": operation.bound_seconds(SEGMENTS),
    }
    saved_row = before
    assert (saved_row.revision_id, saved_row.revision_number, saved_row.program_id) == (
        revision, 2, live.program_id,
    )  # fmt: skip
    assert bytes(saved_row.content_hash) == draft_hash(DRAFT_2)
    assert saved_row.detached_at is None
    # verify recorded the read-back; the creating operation is still the one named
    assert bytes(saved_row.state_hash) == mowl_state.state_hash(mowl_state.normalized_program(read))
    save_ops = uenv.s.operations(live.saved.owner)
    assert saved_row.operation_id == save_ops[0].id
    runs = uenv.sql("SELECT count(*) FROM agent_runs WHERE job_id = :j", j=job_id).scalar_one()
    assert runs == 0


async def test_a_second_update_reads_the_state_the_first_one_verified(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    second = uenv.revision(live)
    first = uenv.job(live, second, DRAFT_2)
    await uenv.run_next()
    assert uenv.row(first).state == "completed"
    third = uenv.revision(live, DRAFT_RENAMED)
    job = uenv.job(live, third, DRAFT_RENAMED)
    await uenv.run_next()
    assert uenv.row(job).state == "completed"
    [program] = fake_programs.owned(TOKEN_A)
    assert program.body["Name"] == DRAFT_RENAMED["name"]
    assert uenv.saved_row(live.saved_workout_id).name == DRAFT_RENAMED["name"]


# --- which program: only the user's own live saved row of the class -------------------------


async def refused_at_creation(
    uenv: UpdateEnv, browser: Any, reason: str, status: int = 409, **kwargs: Any
) -> None:
    owner = browser.live.saved.owner
    before = uenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()
    answer = await browser.update(**kwargs)
    assert answer.status_code == status, answer.text
    assert answer.json()["detail"]["reason"] == reason
    after = uenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()
    assert after == before  # nothing created


async def test_creation_writes_only_the_users_own_live_saved_row_of_the_class(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    other = await uenv.live(TOKEN_B)  # another user's saved row
    sibling = await uenv.sibling(live)  # another class of the user, saved
    browser = await update_browser(live)
    seen = uenv.counts()

    for saved_workout_id in (other.saved_workout_id, sibling.saved_workout_id, uuid.uuid4()):
        await refused_at_creation(
            uenv, browser, "invalid_input", saved_workout_id=str(saved_workout_id)
        )
    # a program id in the input is refused by the input model (422), never read
    await refused_at_creation(uenv, browser, "invalid_input", 422, program_id=other.program_id)
    await refused_at_creation(uenv, browser, "invalid_input", 422, program_id=live.program_id)
    # a detached row (section 9) can't be written to
    uenv.sql(
        "UPDATE saved_workouts SET detached_at = now() WHERE id = :id", id=live.saved_workout_id
    )
    await refused_at_creation(uenv, browser, "invalid_input")
    uenv.sql(
        "UPDATE saved_workouts SET detached_at = NULL WHERE id = :id", id=live.saved_workout_id
    )
    accepted = await browser.update()
    assert accepted.status_code == 201, accepted.text  # the same request on the live row
    assert uenv.counts() == seen  # nothing sent by any refusal (and nothing run yet)


async def test_a_saved_row_whose_creating_operation_is_another_classs_is_refused(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    """The ``operation_id`` foreign key is owner-inclusive, not class-inclusive (#156 review):
    a row that names an operation of another class of the user is inconsistent and refused."""
    live = await uenv.live()
    sibling = await uenv.sibling(live)
    [sibling_op] = [
        o for o in uenv.s.operations(sibling.saved.owner) if o.class_id == sibling.saved.class_id
    ]
    uenv.sql(
        "UPDATE saved_workouts SET operation_id = :op WHERE id = :id",
        op=sibling_op.id, id=live.saved_workout_id,
    )  # fmt: skip
    browser = await update_browser(live)
    seen = uenv.counts()
    await refused_at_creation(uenv, browser, "invalid_input")
    job_id = uenv.job(live)  # past creation: the worker refuses the same way
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == ("failed", "invalid_input")
    assert uenv.operations(live.saved.owner) == []
    assert uenv.counts() == seen


async def run_refused(uenv: UpdateEnv, live: Live, category: str, **overrides: Any) -> UUID:
    job_id = uenv.job(live, **overrides)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", category)
    assert uenv.operations(live.saved.owner) == []
    return job_id


async def test_the_worker_writes_only_the_users_own_live_saved_row_of_the_class(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    other = await uenv.live(TOKEN_B)
    sibling = await uenv.sibling(live)  # another class of the same user, saved
    seen = uenv.counts()
    await run_refused(uenv, live, "invalid_input", saved_workout_id=str(other.saved_workout_id))
    await run_refused(uenv, live, "invalid_input", saved_workout_id=str(sibling.saved_workout_id))
    await run_refused(uenv, live, "invalid_input", saved_workout_id=str(uuid.uuid4()))
    # a stored input carrying a program id does not parse: refused before anything runs
    await run_refused(uenv, live, "invalid_input", program_id=other.program_id)
    uenv.sql(
        "UPDATE saved_workouts SET detached_at = now() WHERE id = :id", id=live.saved_workout_id
    )
    await run_refused(uenv, live, "invalid_input")
    assert uenv.counts() == seen
    for program in fake_programs.programs.values():
        assert program.body["Name"] == DRAFT["name"]  # no program touched


async def test_creation_and_the_worker_refuse_the_other_failed_checks(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    browser = await update_browser(live)
    other = await uenv.live(TOKEN_B)
    seen = uenv.counts()
    await refused_at_creation(uenv, browser, "hash_mismatch", content_hash="0" * 64)
    await refused_at_creation(
        uenv, browser, "invalid_input", revision_id=str(other.saved.revision_id)
    )
    await refused_at_creation(
        uenv, browser, "not_connected", mowl_connection_id=str(other.saved.connection)
    )
    await run_refused(uenv, live, "invalid_input", content_hash="0" * 64)
    await run_refused(uenv, live, "not_connected", mowl_connection_id=str(other.saved.connection))
    uenv.sql("DELETE FROM mowl_playlist_links WHERE id = :id", id=live.saved.link_id)
    await refused_at_creation(uenv, browser, "music_unlinked")
    await run_refused(uenv, live, "music_unlinked")
    assert uenv.counts() == seen


async def test_a_job_whose_class_is_not_its_input_class_is_internal(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    elsewhere = new_class(uenv.s.ops.admin, live.saved.owner)
    job_id = new_job(
        uenv.s.ops.admin, live.saved.owner, elsewhere, kind=saving.UPDATE_KIND,
        input=json.dumps(live.input()), credit_state="none",
    )  # fmt: skip
    seen = uenv.counts()
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == ("failed", "internal")
    assert uenv.counts() == seen


# --- read first -------------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["renamed", "interval", "public", "deleted", "unverified"])
async def test_a_program_changed_in_mowl_is_not_written_unless_overwrite(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, change: str
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    if change == "renamed":
        fake_programs.edit(live.program_id, Name="My own name")
    elif change == "interval":
        fake_programs.edit_interval(live.program_id, zone=1)
    elif change == "public":
        fake_programs.publish(live.program_id)
    elif change == "deleted":
        fake_programs.delete(live.program_id)
    else:  # classbeat could not check it after the last write
        uenv.sql(
            "UPDATE saved_workouts SET state_hash = NULL WHERE id = :id", id=live.saved_workout_id
        )
    expected = "program_missing" if change == "deleted" else "program_changed"
    writes = fake_programs.write_count
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", expected)  # not retried
    assert uenv.stages(job_id) == ["prepare", "read"]
    assert fake_programs.write_count == writes  # nothing written
    assert uenv.operations(live.saved.owner) == []
    assert uenv.saved_row(live.saved_workout_id).revision_id == live.saved.revision_id

    # "Update anyway": the same request with the page's confirmation writes (except a program
    # that is gone, which stays missing).
    again = uenv.job(live, revision, DRAFT_2, overwrite=True)
    await uenv.run_next()
    row = uenv.row(again)
    if change == "deleted":
        assert (row.state, row.error_category) == ("failed", "program_missing")
        assert fake_programs.write_count == writes
        return
    assert (row.state, row.error_category) == ("completed", None)
    program = fake_programs.programs[live.program_id]
    read = fake_programs.read_program(program)
    assert mowl_state.matches(spec_of(DRAFT_2), read, playlist_id=LINKED_PLAYLIST)
    assert program.public is False  # the library writes it private again
    [op] = uenv.operations(live.saved.owner)
    assert (
        op.request_context["before_hash"]
        != bytes(uenv.saved_row(live.saved_workout_id).state_hash).hex()
    )
    assert len(fake_programs.owned(TOKEN_A)) == 1


async def test_a_program_not_served_to_this_account_is_missing(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    fake_programs.programs[live.program_id].token = TOKEN_B  # MOWL answers "No program found"
    writes = fake_programs.write_count
    job_id = uenv.job(live, overwrite=True)
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == (
        "failed", "program_missing",
    )  # fmt: skip
    assert fake_programs.write_count == writes


async def test_a_failing_read_is_retryable_and_writes_nothing(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    fake_programs.get_status = 503
    writes = fake_programs.write_count
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert uenv.row(job_id).state == "queued"
    [*_, (_, kind, payload, _)] = uenv.s.ops.events(job_id)
    assert (kind, payload["category"]) == ("retry_scheduled", "upstream_unavailable")
    assert fake_programs.write_count == writes
    assert uenv.operations(live.saved.owner) == []
    fake_programs.get_status = None
    uenv.ready(job_id)
    await uenv.run_next()
    assert uenv.row(job_id).state == "completed"


async def test_a_preflight_failure_is_retryable_with_no_operation_row(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    fake_programs.list_status = 503
    writes, gets = uenv.counts()
    job_id = uenv.job(live)
    await uenv.run_next()
    assert uenv.row(job_id).state == "queued"
    assert uenv.counts() == (writes, gets)  # no read, no write
    assert uenv.operations(live.saved.owner) == []


# --- reconnect, the unresolved-write rule, the request hash -----------------------------------


async def test_after_a_reconnect_the_saved_rows_are_detached_and_update_is_refused(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    browser = await update_browser(live)
    queued = await browser.update()  # created before the reconnect
    assert queued.status_code == 201
    await uenv.s.ops.connect(live.saved.owner, TOKEN_A)  # a reconnect, even to the same account
    row = uenv.saved_row(live.saved_workout_id)
    assert row.detached_at is not None
    seen = uenv.counts()
    await uenv.run_next()
    job = uenv.row(UUID(queued.json()["id"]))
    assert (job.state, job.error_category) == ("failed", "invalid_input")
    # a new request: the row is detached (and the browser's session names the new connection)
    connection = uenv.sql(
        "SELECT id FROM connections WHERE owner_id = :o AND provider = 'mowl'",
        o=live.saved.owner,
    ).scalar_one()
    fresh = await update_browser(live)
    answer = await fresh.update(mowl_connection_id=str(connection))
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "invalid_input")
    assert uenv.counts() == seen
    assert uenv.operations(live.saved.owner) == []


async def test_an_unresolved_save_or_update_of_the_class_refuses_the_update(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any, make_actor: Any
) -> None:
    live = await uenv.live()
    browser = await update_browser(live)
    revision = uenv.revision(live, DRAFT_RENAMED)
    # an update that stops after its header: a partial state, uncertain
    fake_programs.fail = {fake_programs.write_count + 2: "refuse"}
    first = uenv.job(live, revision, DRAFT_RENAMED)
    await uenv.run_next()
    assert uenv.row(first).error_category == "uncertain_write"
    [op] = uenv.operations(live.saved.owner)
    assert op.state == "uncertain"
    seen = uenv.counts()
    for request in (browser.update(), browser.save()):  # an Update and a Save of the class
        answer = await request
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "write_unresolved")
    blocked = uenv.job(live)  # past creation: prepare refuses
    await uenv.run_next()
    assert (uenv.row(blocked).state, uenv.row(blocked).error_category) == (
        "failed", "uncertain_write",
    )  # fmt: skip
    assert uenv.counts() == seen
    # a pending row of another job (an orphan of a reaped job) refuses it too
    uenv.sql("UPDATE external_operations SET state = 'pending' WHERE id = :id", id=op.id)
    answer = await browser.update()
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "write_unresolved")


async def test_a_retry_that_would_write_another_playlist_is_a_hash_mismatch(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    fake_programs.fail = {fake_programs.write_count + 1: "refuse"}  # nothing applied: failed
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert uenv.op_states(job_id) == ["pending", "uncertain", "failed"]
    assert uenv.row(job_id).state == "queued"  # retried: no effect was proved
    # before the retry the link now names another playlist (the guard sees the same new value)
    uenv.sql(
        "UPDATE mowl_playlist_links SET playlist_id = :p WHERE id = :id",
        p=LINKED_PLAYLIST + 7, id=live.saved.link_id,
    )  # fmt: skip
    writes = fake_programs.write_count
    uenv.ready(job_id)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "internal")
    assert fake_programs.write_count == writes  # no write
    [op] = uenv.operations(live.saved.owner)
    assert op.state == "failed"
    assert op.request_context["playlist_id"] == LINKED_PLAYLIST


@pytest.mark.parametrize("change", ["detached", "program", "link"])
async def test_the_decide_guard_refuses_a_change_after_prepare_before_any_row(
    uenv: UpdateEnv,
    fake_programs: FakeMowlPrograms,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Section 5.3: between ``prepare`` and the write the saved row was detached or now names
    another program, or the link another playlist: ``not_connected`` before ``pending``."""
    live = await uenv.live()
    real = update_kind.perform

    async def changed_then_perform(run: Any, request: Any, call: Any, **kwargs: Any) -> Any:
        if change == "detached":
            uenv.sql(
                "UPDATE saved_workouts SET detached_at = now() WHERE id = :id",
                id=live.saved_workout_id,
            )
        elif change == "program":
            uenv.sql(
                "UPDATE saved_workouts SET program_id = program_id + 1 WHERE id = :id",
                id=live.saved_workout_id,
            )
        else:
            uenv.sql(
                "UPDATE mowl_playlist_links SET playlist_id = 999 WHERE id = :id",
                id=live.saved.link_id,
            )
        return await real(run, request, call, **kwargs)

    monkeypatch.setattr(update_kind, "perform", changed_then_perform)
    writes = fake_programs.write_count
    job_id = uenv.job(live)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert uenv.operations(live.saved.owner) == []
    assert fake_programs.write_count == writes


async def test_admit_refuses_a_connection_that_is_not_the_contexts(uenv: UpdateEnv) -> None:
    from classbeat.db import user_session

    live = await uenv.live()
    parsed = saving.UpdateWorkoutInput.model_validate(live.input())
    named = make_context(live.saved.owner, connections=[("mowl", live.saved.connection)])
    unnamed = make_context(live.saved.owner)
    for ctx, expected in ((unnamed, "not_connected"), (named, None)):
        async with user_session(uenv.s.ops.factory, ctx) as db:
            result = await update_kind.admit(db, ctx, parsed)
        if expected is None:
            assert isinstance(result, update_kind.Admitted)
        else:
            assert isinstance(result, update_kind.Refused)
            assert result.reason == expected
