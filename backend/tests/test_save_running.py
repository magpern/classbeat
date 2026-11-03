"""Save and Update of a running class (ADR 0018 sections 2, 5 and Amendment 2, M8 R1b, #490;
ADR 0007 sections 5 to 8): the class's sport is its stored brief's, the library writes a running
program (its ``ActivityTypeID`` 1, segments 0, running interval bodies with a float ``Gradient``)
on the playlist of the class's existing link and never attaches music; the reconcilers, the
read-back and ``state_hash`` handle running programs while every cycling hash and request stays
as it was; Update never crosses sports, ``overwrite`` included; and ``running_enabled`` off is
``feature_off``. The in-process fake MOWL (``savetools``); never the network.
"""

import copy
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from mowl import workout

from classbeat import mowl_state
from classbeat.db import user_session
from classbeat.design import sport
from classbeat.operations import Operation, ReconcileInput, Reconciliation
from classbeat.saving import approval, operation, update_operation
from ctx import make_context
from optools import TOKEN_A, TOKEN_B
from savetools import (
    DRAFT,
    LINKED_PLAYLIST,
    RUNNING_BRIEF,
    RUNNING_DRAFT,
    FakeMowlPrograms,
    Saved,
    SaveEnv,
    draft_hash,
    running_spec_of,
    spec_of,
)
from updatetools import DRAFT_2, RUNNING_DRAFT_2, Live, UpdateEnv

RUN_SPEC = running_spec_of(RUNNING_DRAFT)
SEGMENTS = len(RUNNING_DRAFT["segments"])
WRITES = 2 + 3 * SEGMENTS  # the requests ``workout.save`` makes for RUNNING_DRAFT
# The normal form's SHA-256 of the cycling DRAFT and DRAFT_2 with playlist 7, as computed by
# ``mowl_state`` on main before R1b: a cycling hash must never change (stored ``state_hash``).
CYCLING_HASHES = {
    "DRAFT": "e41c234a781dcaa42454f937f97faa7c5199c00dbf87fe1eab0ee7ff787f6d46",
    "DRAFT_2": "c88baab1a6813f0d62502816e13533eaa2d4999db88d730c71387bf32109f709",
}


@pytest.fixture(autouse=True)
def running_on(senv: SaveEnv) -> Iterator[None]:
    """The environment's switch on (after ``senv``, whose app set it from its settings, off);
    no stored admin flag, so the effective flag is on."""
    sport.configure(sport.SportGate(running_enabled=True))
    admin_flag(senv, None)  # app_settings outlives a test: start and end without the row
    try:
        yield
    finally:
        admin_flag(senv, None)
        sport.configure(None)


def admin_flag(senv: SaveEnv, value: str | None) -> None:
    senv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    if value is not None:
        senv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb))",
            v=json.dumps(value),
        )


def switch_off(senv: SaveEnv, how: str) -> None:
    if how == "environment":
        sport.configure(sport.SportGate(running_enabled=False))
    else:
        admin_flag(senv, "off")


def writes_sent(fake: FakeMowlPrograms, since: int = 0) -> list[str]:
    return [r for r in fake.requests[since:] if r.split(" ", 1)[0] in ("PUT", "DELETE")]


def segment_bodies(fake: FakeMowlPrograms, program_id: int) -> list[Any]:
    return [fake.segments[s] for s in fake.programs[program_id].segment_ids]


# --- the normal form: running added, cycling unchanged ------------------------------------------


@pytest.mark.parametrize(("name", "content"), [("DRAFT", DRAFT), ("DRAFT_2", DRAFT_2)])
def test_a_cycling_hash_is_byte_for_byte_what_it_was(name: str, content: dict[str, Any]) -> None:
    spec = spec_of(content)
    normal = mowl_state.normalized_spec(spec, 7)
    assert "sport" not in normal
    assert mowl_state.state_hash(normal).hex() == CYCLING_HASHES[name]
    fake = FakeMowlPrograms()
    read = fake.read_program(fake.programs[fake.plant("t", spec, playlist_id=7)])
    assert read["ActivityTypeID"] == 0
    assert mowl_state.state_hash(mowl_state.normalized_program(read)).hex() == CYCLING_HASHES[name]
    read.pop("ActivityTypeID")  # a program read before running had none: the same hash
    assert mowl_state.state_hash(mowl_state.normalized_program(read)).hex() == CYCLING_HASHES[name]


def test_a_running_program_reads_back_as_its_spec_with_gradient_and_sport() -> None:
    fake = FakeMowlPrograms()
    read = fake.read_program(fake.programs[fake.plant("t", RUN_SPEC, playlist_id=7)])
    assert read["ActivityTypeID"] == 1
    gradients = [i["Gradient"] for s in read["Segments"] for i in s["Intervals"]]
    assert gradients == [0.0, 1.0, 0.0, 0.0, 0.0]
    assert all(type(g) is float for g in gradients)  # as MOWL reads them back
    normal = mowl_state.normalized_program(read)
    assert normal == mowl_state.normalized_spec(RUN_SPEC, 7)
    assert normal["sport"] == "running"
    assert normal["segments"][1]["intervals"][0] == {"sec": 240, "zone": 4, "gradient": 1}
    assert mowl_state.matches(RUN_SPEC, read, playlist_id=7)
    # The incline is part of the state: a moved incline is another program.
    other = running_spec_of(RUNNING_DRAFT_2)
    assert not mowl_state.matches(other, read, playlist_id=7)
    assert mowl_state.state_hash(normal) != mowl_state.state_hash(
        mowl_state.normalized_spec(other, 7)
    )


def test_a_ride_never_matches_a_run_either_way() -> None:
    fake = FakeMowlPrograms()
    run = fake.read_program(fake.programs[fake.plant("t", RUN_SPEC, playlist_id=7)])
    ride = fake.read_program(fake.programs[fake.plant("t", spec_of(DRAFT), playlist_id=7)])
    as_ride = {k: v for k, v in RUN_SPEC.items() if k != "sport"}  # the same intervals, no sport
    as_ride["segments"] = [
        {**s, "intervals": [{"sec": i["sec"], "zone": i["zone"]} for i in s["intervals"]]}
        for s in RUN_SPEC["segments"]
    ]
    assert not mowl_state.matches(as_ride, run, playlist_id=7)
    assert not mowl_state.matches(RUN_SPEC, ride, playlist_id=7)
    run_as_ride = copy.deepcopy(run)
    run_as_ride["ActivityTypeID"] = 0  # the same bodies, read as a ride
    assert not mowl_state.matches(RUN_SPEC, run_as_ride, playlist_id=7)


@pytest.mark.parametrize("activity", [2, -1, True, 1.0, "1"])
def test_a_program_of_an_unknown_activity_type_is_not_normalizable(activity: Any) -> None:
    fake = FakeMowlPrograms()
    read = fake.read_program(fake.programs[fake.plant("t", RUN_SPEC, playlist_id=7)])
    read["ActivityTypeID"] = activity
    with pytest.raises(ValueError, match="activity type"):
        mowl_state.normalized_program(read)
    assert not mowl_state.matches(RUN_SPEC, read, playlist_id=7)


@pytest.mark.parametrize("gradient", [0.5, 2, -1, True, None, "1"])
def test_a_running_interval_without_a_known_gradient_is_not_normalizable(gradient: Any) -> None:
    fake = FakeMowlPrograms()
    read = fake.read_program(fake.programs[fake.plant("t", RUN_SPEC, playlist_id=7)])
    read["Segments"][0]["Intervals"][0]["Gradient"] = gradient
    with pytest.raises(ValueError, match="gradient"):
        mowl_state.normalized_program(read)
    spec = copy.deepcopy(RUN_SPEC)
    spec["segments"][0]["intervals"][0]["gradient"] = gradient
    with pytest.raises(ValueError, match="gradient"):
        mowl_state.normalized_spec(spec, 7)


def test_a_running_update_bound_counts_the_librarys_read() -> None:
    assert operation.bound_seconds(3, reads=1) == operation.bound_seconds(3) + 30
    assert operation.bound_seconds(3, reads=0) == operation.bound_seconds(3) == 30 * 12
    assert operation.bound_seconds(20, reads=1) == 1800  # still capped
    with pytest.raises(ValueError, match="refunded"):
        operation.bound_seconds(3, reads=-1)


# --- Save: create a running program --------------------------------------------------------------


async def test_a_running_class_is_saved_as_a_running_program(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.running_class()
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert senv.op_states(job_id) == ["pending", "succeeded"]

    [program] = fake_programs.owned(TOKEN_A)
    header = program.body
    assert header["ActivityTypeID"] == workout.RUNNING_ACTIVITY_TYPE == 1
    assert header["PlaylistID"] == LINKED_PLAYLIST  # the class's existing playlist, nothing new
    assert (header["IsPublic"], program.public) == (False, False)
    assert header["TSS"] == workout.tss(RUN_SPEC)
    segments = segment_bodies(fake_programs, program.id)
    assert [s.body["ActivityTypeID"] for s in segments] == [0] * SEGMENTS  # never 1
    sent = [i for s in segments for i in s.intervals]
    expected = [
        workout.running_interval_body(i) for s in RUN_SPEC["segments"] for i in s["intervals"]
    ]
    assert sent == expected
    assert [i["Gradient"] for i in sent] == [0.0, 1.0, 0.0, 0.0, 0.0]
    assert {(i["RPMFrom"], i["RPMTo"], i["IntervalGame"], i["FtpTest"]) for i in sent} == {
        ("0", "0", None, None)
    }
    # Only the save's own requests: no attach, no analysis, no other MOWL object.
    assert len(fake_programs.writes) == WRITES
    assert fake_programs.writes[0].split(" ", 2)[2] == "/v1/programs"
    assert all("/v1/Spotify" not in r and "Swedebeat" not in r for r in fake_programs.requests)

    [saved_row] = senv.saved_rows(saved.owner)
    assert (saved_row.program_id, saved_row.playlist_id) == (program.id, LINKED_PLAYLIST)
    want = mowl_state.state_hash(mowl_state.normalized_spec(RUN_SPEC, LINKED_PLAYLIST))
    assert bytes(saved_row.state_hash) == want  # the read-back matched the running form


async def test_a_cycling_save_still_sends_exactly_the_cycling_requests(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    """The golden cycling save: a legacy class (no brief) and a class whose brief says cycling
    write the same program, header ``ActivityTypeID`` 0, the cycling interval bodies with an int
    ``Gradient`` 0, and the same requests; neither reads the running flag."""
    admin_flag(senv, "off")  # running off changes nothing for a ride
    results = []
    for token, brief in ((TOKEN_A, None), (TOKEN_B, {"length_min": 45, "sport": "cycling"})):
        owner, connection = await senv.owner(token)
        saved = senv.plant(owner, connection, brief=brief)
        before = len(fake_programs.writes)
        job_id = senv.job(saved)
        await senv.run_next()
        assert (senv.row(job_id).state, senv.row(job_id).error_category) == ("completed", None)
        writes = [w.split(" ", 1)[1] for w in fake_programs.writes[before:]]
        program = max(fake_programs.programs.values(), key=lambda p: p.id)
        results.append((writes, program))
    for writes, program in results:
        assert [w.split(" ")[0] for w in writes] == (
            ["PUT"] + ["PUT", "DELETE", "PUT"] * len(DRAFT["segments"]) + ["PUT"]
        )
        assert program.body == {
            "ProgramCategoryID": 17,
            "Name": DRAFT["name"],
            "Description": DRAFT["description"],
            "ShortDescription": "",
            "PlaylistID": LINKED_PLAYLIST,
            "IsPublic": False,
            "TSS": workout.tss(spec_of(DRAFT)),
            "ActivityTypeID": 0,
        }
        intervals = [i for s in segment_bodies(fake_programs, program.id) for i in s.intervals]
        assert intervals == [
            workout.interval_body(i) for s in spec_of(DRAFT)["segments"] for i in s["intervals"]
        ]
        assert {type(i["Gradient"]) for i in intervals} == {int}
    assert [p.body["ActivityTypeID"] for p in fake_programs.programs.values()] == [0, 0]


async def test_a_lost_last_answer_is_uncertain_then_reconciled_to_the_running_program(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.running_class()
    fake_programs.fail = {WRITES: "drop"}  # every request applied, the last answer lost
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.op_states(job_id) == ["pending", "uncertain", "succeeded"]
    [op] = senv.operations(saved.owner)
    [program] = fake_programs.owned(TOKEN_A)
    assert (op.state, op.resolved_by, op.external_id) == ("succeeded", "worker", str(program.id))
    assert senv.row(job_id).state == "completed"
    [saved_row] = senv.saved_rows(saved.owner)
    assert saved_row.program_id == program.id
    assert program.body["ActivityTypeID"] == 1


@pytest.mark.parametrize("number", [1, 2, WRITES - 1])
async def test_a_failure_mid_write_is_uncertain_never_failed(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, number: int
) -> None:
    saved = await senv.running_class()
    fake_programs.fail = {number: "refuse"}
    job_id = senv.job(saved)
    await senv.run_next()
    states = senv.op_states(job_id)
    assert states[:2] == ["pending", "uncertain"]
    assert "failed" not in states
    assert (senv.row(job_id).state, senv.row(job_id).error_category) == (
        "failed", "uncertain_write",
    )  # fmt: skip
    assert senv.saved_rows(saved.owner) == []


def save_operation(saved: Saved, baseline: list[int]) -> Operation:
    now = datetime.now(UTC)
    return Operation(
        id=uuid.uuid4(),
        kind=operation.KIND,
        idempotency_key=f"{uuid.uuid4()}:save",
        state="uncertain",
        external_id=None,
        job_id=None,
        class_id=saved.class_id,
        context={
            **operation.save_context(saved.revision_id, saved.hash_hex, LINKED_PLAYLIST, baseline),
            "bound_seconds": 180,
        },
        pending_at=now - timedelta(seconds=3660),
        call_ended_at=now - timedelta(seconds=3600),
    )


async def reconcile_save(senv: SaveEnv, saved: Saved, op: Operation) -> Reconciliation:
    check = ReconcileInput(senv.ops.factory, senv.ops.keyring, make_context(saved.owner), op)
    return await operation.SAVE.reconcile(check)


async def test_the_save_reconciler_finds_the_running_program_and_never_a_ride(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.running_class()
    old = fake_programs.plant(TOKEN_A, name="Older")
    # A ride with the run's name and lengths is never the effect of a running save.
    ride = copy.deepcopy(spec_of(DRAFT))
    ride["name"] = RUN_SPEC["name"]
    fake_programs.plant(TOKEN_A, ride, playlist_id=LINKED_PLAYLIST)
    assert await reconcile_save(senv, saved, save_operation(saved, [old])) == Reconciliation(
        "inconclusive"
    )
    run = fake_programs.plant(TOKEN_A, RUN_SPEC, playlist_id=LINKED_PLAYLIST)
    got = await reconcile_save(senv, saved, save_operation(saved, [old]))
    assert got == Reconciliation("succeeded", str(run))
    # The listing marks each entry's sport, and the reconciler ignores it (same request).
    assert fake_programs.lists >= 2


# --- Update: a running program -------------------------------------------------------------


async def test_a_running_program_is_updated_in_place_and_stays_running(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.running_live()
    revision = uenv.revision(live, RUNNING_DRAFT_2)
    since = len(fake_programs.requests)
    job_id = uenv.job(live, revision, RUNNING_DRAFT_2)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert uenv.stages(job_id) == ["prepare", "read", "save", "verify"]
    assert len(fake_programs.owned(TOKEN_A)) == 1
    program = fake_programs.programs[live.program_id]
    assert program.body["ActivityTypeID"] == 1
    read = fake_programs.read_program(program)
    assert mowl_state.matches(running_spec_of(RUNNING_DRAFT_2), read, playlist_id=LINKED_PLAYLIST)
    assert [s.body["ActivityTypeID"] for s in segment_bodies(fake_programs, program.id)] == [0] * 3
    # The read stage's GET, then the library's own GET of the target before its first write.
    sent = [r.split(" ", 1)[0] for r in fake_programs.requests[since:]]
    first_write = sent.index("PUT")
    assert sent[:first_write].count("GET") == 2
    saved_row = uenv.saved_row(live.saved_workout_id)
    assert saved_row.revision_id == revision
    want = mowl_state.state_hash(
        mowl_state.normalized_spec(running_spec_of(RUNNING_DRAFT_2), LINKED_PLAYLIST)
    )
    assert bytes(saved_row.state_hash) == want
    [op] = uenv.operations(live.saved.owner)
    assert op.request_context["bound_seconds"] == operation.bound_seconds(3, reads=1)


async def test_a_cycling_update_sends_no_read_inside_the_write(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    """The golden cycling update: one read (the read stage), then the writes; the library gets
    no ``target`` and makes no read of its own, and the bound is the cycling one."""
    live = await uenv.live()
    revision = uenv.revision(live)
    since = len(fake_programs.requests)
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert uenv.row(job_id).state == "completed"
    sent = [r.split(" ", 1)[0] for r in fake_programs.requests[since:]]
    first_write = sent.index("PUT")
    assert sent[:first_write].count("GET") == 1
    assert sent[first_write:].count("GET") == 1  # the verify read only
    assert fake_programs.programs[live.program_id].body["ActivityTypeID"] == 0
    [op] = uenv.operations(live.saved.owner)
    assert op.request_context["bound_seconds"] == operation.bound_seconds(3)


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("activity", [0, None, 2, True, 1.0, "1"])
async def test_a_running_class_never_writes_a_program_of_another_or_unknown_sport(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, activity: Any, overwrite: bool
) -> None:
    live = await uenv.running_live()
    revision = uenv.revision(live, RUNNING_DRAFT_2)
    fake_programs.set_activity_type(live.program_id, activity)
    before = copy.deepcopy(fake_programs.read_program(fake_programs.programs[live.program_id]))
    writes, since = fake_programs.write_count, len(fake_programs.requests)
    job_id = uenv.job(live, revision, RUNNING_DRAFT_2, overwrite=overwrite)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "program_other_sport")
    assert uenv.stages(job_id) == ["prepare", "read"]
    assert fake_programs.write_count == writes
    assert writes_sent(fake_programs, since) == []
    assert fake_programs.read_program(fake_programs.programs[live.program_id]) == before
    assert uenv.operations(live.saved.owner) == []


@pytest.mark.parametrize("overwrite", [False, True])
async def test_a_cycling_class_never_writes_a_running_program(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, overwrite: bool
) -> None:
    """The reverse direction, with a real running program (as Save writes it) as the target."""
    live = await uenv.live()
    revision = uenv.revision(live)
    run = fake_programs.programs[
        fake_programs.plant(TOKEN_A, RUN_SPEC, playlist_id=LINKED_PLAYLIST)
    ]
    target = fake_programs.programs[live.program_id]
    target.body, target.segment_ids = run.body, run.segment_ids  # the saved program is now a run
    writes = fake_programs.write_count
    job_id = uenv.job(live, revision, DRAFT_2, overwrite=overwrite)
    await uenv.run_next()
    assert uenv.row(job_id).error_category == "program_other_sport"
    assert fake_programs.write_count == writes
    assert target.body["ActivityTypeID"] == 1


async def test_a_running_program_changed_in_the_app_is_program_changed_unless_overwrite(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.running_live()
    revision = uenv.revision(live, RUNNING_DRAFT_2)
    segment = fake_programs.segments[fake_programs.programs[live.program_id].segment_ids[0]]
    segment.intervals[0] = {**segment.intervals[0], "Gradient": 1.0}  # the incline moved in MOWL
    writes = fake_programs.write_count
    job_id = uenv.job(live, revision, RUNNING_DRAFT_2)
    await uenv.run_next()
    assert uenv.row(job_id).error_category == "program_changed"
    assert fake_programs.write_count == writes
    job_id = uenv.job(live, revision, RUNNING_DRAFT_2, overwrite=True)
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == ("completed", None)
    assert fake_programs.programs[live.program_id].body["ActivityTypeID"] == 1


async def test_a_lost_update_answer_is_reconciled_to_the_running_program(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.running_live()
    revision = uenv.revision(live, RUNNING_DRAFT_2)
    fake_programs.fail = {fake_programs.write_count + WRITES: "drop"}
    job_id = uenv.job(live, revision, RUNNING_DRAFT_2)
    await uenv.run_next()
    assert uenv.op_states(job_id) == ["pending", "uncertain", "succeeded"]
    assert uenv.row(job_id).state == "completed"
    assert uenv.saved_row(live.saved_workout_id).revision_id == revision


def update_op(uenv: UpdateEnv, live: Live, revision_id: UUID, content: dict[str, Any]) -> Operation:
    program = uenv.fake.read_program(uenv.fake.programs[live.program_id])
    base = update_operation.update_context(
        live.saved_workout_id,
        revision_id,
        draft_hash(content).hex(),
        LINKED_PLAYLIST,
        live.program_id,
        update_operation.state_hex(program),
        [live.program_id],
    )
    return Operation(
        id=uuid.uuid4(),
        kind=update_operation.KIND,
        idempotency_key=f"{uuid.uuid4()}:update",
        state="uncertain",
        external_id=None,
        job_id=None,
        class_id=live.saved.class_id,
        context={**base, "bound_seconds": 210},
    )


async def test_the_update_reconciler_decides_a_running_write_both_ways(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.running_live()
    revision = uenv.revision(live, RUNNING_DRAFT_2)
    op = update_op(uenv, live, revision, RUNNING_DRAFT_2)
    assert (
        update_operation.state_hex(
            fake_programs.read_program(fake_programs.programs[live.program_id])
        )
        is not None
    )  # a running program normalizes
    check = ReconcileInput(
        uenv.s.ops.factory, uenv.s.ops.keyring, make_context(live.saved.owner), op
    )
    # Unchanged, nothing new in the listing: provably no effect, even with the flag off now.
    admin_flag(uenv.s, "off")
    assert await update_operation.UPDATE.reconcile(check) == Reconciliation("failed")
    admin_flag(uenv.s, None)
    fresh = fake_programs.plant(
        TOKEN_A, running_spec_of(RUNNING_DRAFT_2), playlist_id=LINKED_PLAYLIST
    )
    made = fake_programs.programs.pop(fresh)
    target = fake_programs.programs[live.program_id]
    target.body, target.segment_ids = made.body, made.segment_ids
    got = await update_operation.UPDATE.reconcile(check)
    assert got == Reconciliation("succeeded", str(live.program_id))


# --- refusals: music, sport and the flag ---------------------------------------------------


def _mix_music(link_id: UUID) -> dict[str, Any]:
    return {
        "source": "mowl_swedebeat_mix",
        "mix_id": "mix-4242",
        "name": "Invented mix",
        "duration": 1800.0,
        "listed_duration": 1800.0,
        "unsure_beat_one": [],
        "fingerprint": "b" * 64,
        "link_id": str(link_id),
    }


def _picked_music(link_id: UUID) -> dict[str, Any]:
    return {
        "source": "mowl_spotify_playlist",
        "link_id": str(link_id),
        "fingerprint": "a" * 64,
        "unknown_bpm": [],
        "song_list_id": str(uuid.uuid4()),
    }


@pytest.mark.parametrize("music", ["mix", "picked"])
async def test_a_running_class_with_new_music_is_never_saved(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any, music: str
) -> None:
    """Running v1 saves on an existing MOWL playlist only (ADR 0018 Amendment 2): a revision on
    a Swedebeat mix or on picked songs is ``invalid_input`` at creation and in the worker."""
    saved = await senv.running_class()
    link = senv.sql(
        "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
        "VALUES (:id, :o, 'swedebeat', 'mix-4242', 777) RETURNING id",
        id=uuid.uuid4(), o=saved.owner,
    ).scalar_one() if music == "mix" else saved.link_id  # fmt: skip
    body = _mix_music(link) if music == "mix" else _picked_music(link)
    revision = senv.plant_revision(saved.owner, saved.class_id, link, RUNNING_DRAFT, music=body)
    browser = await save_browser(saved)
    refused = await browser.save(revision_id=str(revision))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    job_id = senv.job(saved, revision_id=str(revision))
    await senv.run_next()
    assert (senv.row(job_id).state, senv.row(job_id).error_category) == ("failed", "invalid_input")
    assert (fake_programs.lists, fake_programs.write_count, fake_programs.programs) == (0, 0, {})


@pytest.mark.parametrize(
    ("brief", "content"),
    [
        ({"sport": "running"}, DRAFT),  # a running class whose content is a ride
        ({"sport": "cycling"}, RUNNING_DRAFT),  # a ride whose content is a run
        (None, RUNNING_DRAFT),  # a legacy class is cycling, never running
        ({"sport": "swimming"}, DRAFT),  # a sport that is neither
        ({"sport": "Running"}, RUNNING_DRAFT),
    ],
)
async def test_the_sport_is_the_briefs_and_content_of_the_other_sport_is_refused(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, brief: Any, content: dict[str, Any]
) -> None:
    owner, connection = await senv.owner()
    saved = senv.plant(
        owner, connection, content, brief=None if brief is None else {**RUNNING_BRIEF, **brief}
    )
    job_id = senv.job(saved)
    await senv.run_next()
    assert (senv.row(job_id).state, senv.row(job_id).error_category) == ("failed", "invalid_input")
    assert fake_programs.programs == {}
    assert fake_programs.lists == 0  # refused before the preflight


async def test_the_revision_check_reads_the_sport_and_rebuilds_the_running_spec(
    senv: SaveEnv,
) -> None:
    saved = await senv.running_class()
    ctx = make_context(saved.owner)
    digest = bytes.fromhex(saved.hash_hex)
    async with user_session(senv.ops.factory, ctx) as db:
        found = await approval.revision(db, ctx, saved.class_id, saved.revision_id, digest)
    assert isinstance(found, approval.Revision)
    assert (found.sport, found.running, found.spec) == ("running", True, RUN_SPEC)


@pytest.mark.parametrize("off", ["environment", "database"])
async def test_save_and_update_of_a_running_class_are_feature_off_while_running_is_off(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any, off: str
) -> None:
    """ADR 0018 section 5: off means ``feature_off``, also for a class designed while it was
    on; at creation (409, no job) and in the worker for a job queued before it went off
    (nothing sent, not even the preflight listing)."""
    live = await uenv.running_live()
    revision = uenv.revision(live, RUNNING_DRAFT_2)
    browser = await update_browser(live)
    try:
        switch_off(uenv.s, off)
        for answer in (await browser.save(), await browser.update(revision, RUNNING_DRAFT_2)):
            assert answer.status_code == 409, answer.text
            assert answer.json()["detail"]["reason"] == "feature_off"
        for queue in (
            lambda: uenv.s.job(live.saved),
            lambda: uenv.job(live, revision, RUNNING_DRAFT_2),
        ):
            sport.configure(sport.SportGate(running_enabled=True))
            admin_flag(uenv.s, None)
            job_id = queue()  # queued while running was on
            switch_off(uenv.s, off)
            since, lists = len(fake_programs.requests), fake_programs.lists
            await uenv.run_next()
            row = uenv.row(job_id)
            assert (row.state, row.error_category) == ("failed", "feature_off")
            assert (fake_programs.requests[since:], fake_programs.lists) == ([], lists)
        # A cycling class is saved and updated as ever (it never reads the flag).
        ride = await uenv.live(TOKEN_B)
        ride_job = uenv.job(ride, uenv.revision(ride), DRAFT_2)
        await uenv.run_next()
        assert uenv.row(ride_job).state == "completed"
    finally:
        sport.configure(sport.SportGate(running_enabled=True))
        admin_flag(uenv.s, None)
    [program] = fake_programs.owned(TOKEN_A)  # the running program, unchanged
    assert mowl_state.matches(
        RUN_SPEC, fake_programs.read_program(program), playlist_id=LINKED_PLAYLIST
    )


async def test_the_class_api_names_every_classs_sport_legacy_rows_cycling(
    senv: SaveEnv, save_browser: Any
) -> None:
    """R1a review finding 7: ``sport`` on every class of the list and the detail; a class
    without a stored brief (every class before ADR 0018) is ``cycling``, never ``running``."""
    running = await senv.running_class()
    legacy = senv.plant(running.owner, running.connection)
    ride = senv.plant(running.owner, running.connection, brief={"length_min": 45})
    browser = (await save_browser(running)).browser
    listed = await browser.get("/api/classes")
    assert listed.status_code == 200, listed.text
    sports = {c["id"]: c["sport"] for c in listed.json()["classes"]}
    assert sports == {
        str(running.class_id): "running",
        str(legacy.class_id): "cycling",
        str(ride.class_id): "cycling",
    }
    for saved, expected in ((running, "running"), (legacy, "cycling"), (ride, "cycling")):
        detail = await browser.get(f"/api/classes/{saved.class_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["sport"] == expected
