"""``save_workout`` (ADR 0007 sections 5 and 6, issue SV1): the happy path through the jobs API
and the worker, the approval's revalidation at creation and in the worker (nothing sent to the
fake when it refuses), and the preflight. The in-process fake MOWL (``savetools``); never the
network.
"""

import copy
import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from classbeat import connections, mowl_state, saving
from classbeat.jobs import KINDS
from classbeat.saving import kind as save_kind
from classbeat.saving import operation
from classbeat.usage import service as usage
from ctx import make_context
from jobtools import new_class, new_job
from optools import TOKEN_A, TOKEN_B
from savetools import (
    DRAFT,
    LINKED_PLAYLIST,
    SEGMENTS,
    WRITES,
    FakeMowlPrograms,
    Saved,
    SaveEnv,
    draft_hash,
    spec_of,
)


def test_the_kind_is_registered_without_credit_and_with_admit() -> None:
    kind = KINDS.get(saving.KIND)
    assert kind is not None
    assert kind is saving.SAVE_WORKOUT
    assert (kind.consumes_credit, kind.stages) == (False, ("prepare", "save", "verify"))
    assert kind.admit is save_kind.admit
    assert kind.server_fields == frozenset()


def test_the_input_names_ids_and_a_hash_only() -> None:
    good = {
        "class_id": str(UUID(int=1)),
        "revision_id": str(UUID(int=2)),
        "content_hash": "ab" * 32,
        "mowl_connection_id": str(UUID(int=3)),
    }
    assert saving.SaveWorkoutInput.model_validate(good).digest == bytes.fromhex("ab" * 32)
    for bad in (
        {**good, "content_hash": "AB" * 32},
        {**good, "content_hash": "ab" * 31},
        {**good, "program_id": 5},
        {**good, "playlist_id": 5},
        {**good, "name": "x"},
        {k: v for k, v in good.items() if k != "class_id"} | {"title": "New"},
    ):
        with pytest.raises(ValidationError):
            saving.SaveWorkoutInput.model_validate(bad)


def test_the_bound_covers_every_request_and_is_capped() -> None:
    assert operation.bound_seconds(1) == 180
    assert operation.bound_seconds(3) == 30 * (3 + 3 * 3)
    assert operation.bound_seconds(19) == 1800
    assert operation.bound_seconds(20) == 1800
    with pytest.raises(ValueError, match="segments"):
        operation.bound_seconds(0)


async def test_happy_path_one_private_program_and_a_saved_row(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    row = senv.row(job_id)
    assert (row.kind, row.credit_state, row.class_id) == ("save_workout", "none", saved.class_id)

    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    stages = [p["stage"] for _, k, p, _ in senv.ops.events(job_id) if k == "stage_started"]
    assert stages == ["prepare", "save", "verify"]
    assert senv.op_states(job_id) == ["pending", "succeeded"]

    [program] = fake_programs.owned(TOKEN_A)
    read = fake_programs.read_program(program)
    assert read["IsPublic"] is False
    assert program.public is False
    assert read["PlaylistID"] == LINKED_PLAYLIST
    assert mowl_state.matches(spec_of(DRAFT), read, playlist_id=LINKED_PLAYLIST)
    assert len(fake_programs.writes) == WRITES
    assert set(fake_programs.write_tokens) == {TOKEN_A}

    [op] = senv.operations(saved.owner)
    assert (op.state, op.external_id, op.class_id, op.job_id) == (
        "succeeded", str(program.id), saved.class_id, job_id,
    )  # fmt: skip
    assert op.idempotency_key == f"{job_id}:save"
    assert bytes(op.request_hash) == operation.save_hash(saved.hash_hex, LINKED_PLAYLIST)
    assert op.request_context == {
        "revision_id": str(saved.revision_id),
        "content_hash": saved.hash_hex,
        "playlist_id": LINKED_PLAYLIST,
        "baseline": [],
        "bound_seconds": operation.bound_seconds(SEGMENTS),
    }
    assert op.call_ended_at >= op.pending_at
    assert op.result == {"segments": SEGMENTS}

    [saved_row] = senv.saved_rows(saved.owner)
    assert (saved_row.class_id, saved_row.program_id, saved_row.playlist_id) == (
        saved.class_id, program.id, LINKED_PLAYLIST,
    )  # fmt: skip
    assert (saved_row.revision_id, saved_row.revision_number, saved_row.name) == (
        saved.revision_id, 1, DRAFT["name"],
    )  # fmt: skip
    assert bytes(saved_row.content_hash) == draft_hash(DRAFT)
    assert saved_row.operation_id == op.id
    assert saved_row.detached_at is None
    # verify: the read-back matched, so the normalized state is recorded
    assert bytes(saved_row.state_hash) == mowl_state.state_hash(mowl_state.normalized_program(read))
    # usage unaffected: no model, no agent run, no usage in the job view
    runs = senv.sql("SELECT count(*) FROM agent_runs WHERE job_id = :j", j=job_id).scalar_one()
    assert runs == 0
    assert await usage.job_usage(senv.ops.factory, make_context(saved.owner), job_id) is None
    view = await browser.browser.get(f"/api/jobs/{job_id}")
    assert view.status_code == 200
    assert view.json()["usage"] is None


async def test_the_baseline_is_my_workouts_first_page_before_the_write(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    old = [fake_programs.plant(TOKEN_A, name=f"Old {i}") for i in range(3)]
    fake_programs.plant(TOKEN_B, name="Someone else's")
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.row(job_id).state == "completed"
    [op] = senv.operations(saved.owner)
    assert op.request_context["baseline"] == sorted(old, reverse=True)  # newest first
    assert fake_programs.lists == 1  # the preflight only; no reconciliation was needed


async def test_verify_leaves_the_state_unchecked_when_the_read_back_fails(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    fake_programs.get_status = 500
    job_id = senv.job(saved)
    await senv.run_next()
    assert (senv.row(job_id).state, senv.op_states(job_id)) == (
        "completed",
        ["pending", "succeeded"],
    )
    [saved_row] = senv.saved_rows(saved.owner)
    assert saved_row.state_hash is None  # "saved, not checked"


async def test_verify_leaves_the_state_unchecked_when_the_program_differs(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = await senv.saved_class()
    real = fake_programs.read_program

    def changed(program: Any) -> dict[str, Any]:
        shown = real(program)
        shown["Name"] = "Renamed in MOWL"
        return shown

    monkeypatch.setattr(fake_programs, "read_program", changed)
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.row(job_id).state == "completed"
    [saved_row] = senv.saved_rows(saved.owner)
    assert saved_row.state_hash is None


async def test_a_second_save_of_the_class_adds_a_second_program(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    """Saving is not idempotent across jobs (section 6): each Save is a new workout."""
    saved = await senv.saved_class()
    first = senv.job(saved)
    await senv.run_next()
    second = senv.job(saved)
    await senv.run_next()
    assert senv.row(first).state == senv.row(second).state == "completed"
    assert len(fake_programs.owned(TOKEN_A)) == 2
    rows = senv.saved_rows(saved.owner)
    assert len({r.program_id for r in rows}) == 2
    [_, op2] = senv.operations(saved.owner)
    assert len(op2.request_context["baseline"]) == 1  # the first save was in its baseline


# --- revalidation at creation (admit) ----------------------------------------------------------


def retried(senv: SaveEnv, job_id: UUID) -> str:
    """The category of the retry the attempt scheduled (the job is queued again)."""
    assert senv.row(job_id).state == "queued"
    [*_, (_, kind, payload, _)] = senv.ops.events(job_id)
    assert kind == "retry_scheduled"
    return payload["category"]


def _counts(fake: FakeMowlPrograms) -> tuple[int, int, int]:
    return len(fake.requests), fake.lists, len(fake.writes)


async def refused_at_creation(
    senv: SaveEnv, browser: Any, reason: str, status: int = 409, **overrides: Any
) -> None:
    jobs = senv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=browser.saved.owner)
    before = jobs.scalar_one()
    answer = await browser.save(**overrides)
    assert answer.status_code == status, answer.text
    assert answer.json()["detail"]["reason"] == reason
    after = senv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=browser.saved.owner)
    assert after.scalar_one() == before  # nothing created


async def test_creation_refuses_every_failed_check_and_sends_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    other = await senv.saved_class(TOKEN_B)  # another user's class and revision
    foreign_class, foreign_revision = str(other.class_id), str(other.revision_id)
    # another class of the same user, and its revision
    second = senv.plant(saved.owner, saved.connection)
    song_list = senv.plant_revision(saved.owner, saved.class_id, saved.link_id, kind="song_list")
    invalid = copy.deepcopy(DRAFT)
    invalid["segments"] = invalid["segments"][:2]  # no cooldown: workout.validate refuses
    stale = senv.plant_revision(saved.owner, saved.class_id, saved.link_id, invalid)
    lying = senv.plant_revision(
        saved.owner, saved.class_id, saved.link_id, content_hash=hashlib.sha256(b"x").digest()
    )

    await refused_at_creation(senv, browser, "class_not_found", 404, class_id=foreign_class)
    await refused_at_creation(senv, browser, "invalid_input", revision_id=foreign_revision)
    await refused_at_creation(senv, browser, "invalid_input", revision_id=str(second.revision_id))
    await refused_at_creation(senv, browser, "invalid_input", revision_id=str(song_list))
    await refused_at_creation(senv, browser, "hash_mismatch", content_hash="0" * 64)
    await refused_at_creation(
        senv,
        browser,
        "invalid_input",
        revision_id=str(stale),
        content_hash=draft_hash(invalid).hex(),
    )
    await refused_at_creation(
        senv,
        browser,
        "invalid_input",
        revision_id=str(lying),
        content_hash=hashlib.sha256(b"x").hexdigest(),
    )
    await refused_at_creation(
        senv, browser, "not_connected", mowl_connection_id=str(other.connection)
    )
    # a missing link (the MOWL account changed since the revision was designed)
    senv.sql("DELETE FROM mowl_playlist_links WHERE id = :id", id=saved.link_id)
    await refused_at_creation(senv, browser, "music_unlinked")
    # a link of another user named by the revision is not the user's link
    foreign_link = senv.plant_revision(saved.owner, saved.class_id, other.link_id)
    await refused_at_creation(senv, browser, "music_unlinked", revision_id=str(foreign_link))
    assert _counts(fake_programs) == (0, 0, 0)


async def test_creation_refuses_a_connection_that_is_not_connected(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    senv.sql(
        "UPDATE connections SET status = 'needs_reconnect' WHERE id = :id", id=saved.connection
    )
    await refused_at_creation(senv, browser, "not_connected")
    assert _counts(fake_programs) == (0, 0, 0)


async def test_a_replay_is_the_same_job(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    first = await browser.save(key="same-key-for-both-clicks")
    again = await browser.save(key="same-key-for-both-clicks")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    busy = await browser.save()  # another click while the first job is open
    assert (busy.status_code, busy.json()["detail"]["reason"]) == (409, "class_busy")


# --- revalidation in the worker (prepare) ------------------------------------------------------


async def run_refused(senv: SaveEnv, saved: Saved, category: str, **overrides: Any) -> UUID:
    job_id = senv.job(saved, **overrides)
    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", category)
    assert senv.operations(saved.owner) == []
    return job_id


async def test_the_worker_revalidates_and_sends_nothing_when_a_check_fails(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    other = await senv.saved_class(TOKEN_B)
    second = senv.plant(saved.owner, saved.connection)
    song_list = senv.plant_revision(saved.owner, saved.class_id, saved.link_id, kind="song_list")
    invalid = copy.deepcopy(DRAFT)
    invalid["segments"][0]["intervals"][0]["sec"] = 0  # not a WorkoutDraft any more
    broken = senv.plant_revision(saved.owner, saved.class_id, saved.link_id, invalid)

    await run_refused(senv, saved, "invalid_input", revision_id=str(other.revision_id))
    await run_refused(senv, saved, "invalid_input", revision_id=str(second.revision_id))
    await run_refused(senv, saved, "invalid_input", revision_id=str(song_list))
    await run_refused(senv, saved, "invalid_input", content_hash="0" * 64)
    await run_refused(
        senv,
        saved,
        "invalid_input",
        revision_id=str(broken),
        content_hash=draft_hash(invalid).hex(),
    )
    await run_refused(senv, saved, "not_connected", mowl_connection_id=str(other.connection))
    assert _counts(fake_programs) == (0, 0, 0)


async def test_a_reconnect_after_creation_is_music_unlinked_and_sends_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    """Section 9: the account changed between the click and the run. The reconnect deletes the
    links, so the revision's music is not in the connected account."""
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 201
    await senv.ops.connect(saved.owner, TOKEN_B)  # reconnect in place as another account
    await senv.run_next()
    row = senv.row(UUID(answer.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "music_unlinked")
    assert _counts(fake_programs) == (0, 0, 0)
    assert fake_programs.owned(TOKEN_B) == []


async def test_a_disconnect_after_creation_is_not_connected_and_sends_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    job_id = senv.job(saved)
    await connections.disconnect(senv.ops.factory, make_context(saved.owner), "mowl")
    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert _counts(fake_programs) == (0, 0, 0)


async def test_a_job_whose_class_is_not_its_input_class_is_internal(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    elsewhere = new_class(senv.ops.admin, saved.owner)
    job_id = new_job(
        senv.ops.admin, saved.owner, elsewhere, kind=saving.KIND,
        input=json.dumps(saved.input()), credit_state="none",
    )  # fmt: skip
    await senv.run_next()
    assert (senv.row(job_id).state, senv.row(job_id).error_category) == ("failed", "internal")
    assert _counts(fake_programs) == (0, 0, 0)


async def test_a_preflight_failure_is_retryable_with_no_operation_row(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    fake_programs.list_status = 503
    job_id = senv.job(saved)
    await senv.run_next()
    assert retried(senv, job_id) == "upstream_unavailable"
    assert senv.operations(saved.owner) == []
    assert (fake_programs.lists, fake_programs.writes) == (1, [])
    fake_programs.list_status = None
    senv.ready(job_id)
    await senv.run_next()
    assert senv.row(job_id).state == "completed"
    assert len(fake_programs.owned(TOKEN_A)) == 1


async def test_a_malformed_listing_is_retryable_and_writes_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A baseline that misses an id could make an old program look new: no write without it."""
    saved = await senv.saved_class()
    fake_programs.plant(TOKEN_A)
    monkeypatch.setattr(operation, "listed_ids", lambda listing: None)
    job_id = senv.job(saved)
    await senv.run_next()
    assert retried(senv, job_id) == "upstream_unavailable"
    assert fake_programs.writes == []
    assert senv.operations(saved.owner) == []


def test_the_listing_lag_setting_is_unset_by_default_and_bounded() -> None:
    from authtools import make_settings

    url = "postgresql+psycopg://u:p@127.0.0.1:1/x"
    assert make_settings(url).mowl_listing_lag_seconds is None
    assert make_settings(url, mowl_listing_lag_seconds=120).mowl_listing_lag_seconds == 120
    for bad in (0, -1, 86401):
        with pytest.raises(ValidationError):
            make_settings(url, mowl_listing_lag_seconds=bad)
    with pytest.raises(ValueError, match="positive"):
        saving.configure_listing_lag(0)


def test_the_web_app_configures_the_reconcilers_listing_lag() -> None:
    from authtools import make_settings
    from classbeat.web import create_app

    url = "postgresql+psycopg://u:p@127.0.0.1:1/x"
    try:
        create_app(make_settings(url, mowl_listing_lag_seconds=42))
        assert operation._lag == 42  # pyright: ignore[reportPrivateUsage]
        create_app(make_settings(url))
        assert operation._lag is None  # pyright: ignore[reportPrivateUsage]
    finally:
        saving.configure_listing_lag(None)


async def test_the_worker_gives_the_kind_its_key_ring_and_the_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from authtools import make_settings
    from classbeat.worker import __main__ as worker_main

    ring: Any = object()

    class Engine:
        async def dispose(self) -> None:
            return None

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    async def keyring(*args: Any, **kwargs: Any) -> Any:
        return ring

    seen: list[Any] = []

    async def run_worker(settings: Any, stop: Any) -> None:
        seen.append((save_kind._runtime, operation._lag))  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(worker_main, "create_engine", lambda settings: Engine())
    monkeypatch.setattr(worker_main, "verify_database_role", nothing)
    monkeypatch.setattr(worker_main, "create_session_factory", lambda engine: None)
    monkeypatch.setattr(worker_main, "load_keyring", keyring)
    monkeypatch.setattr(worker_main.design, "runtime_from_settings", lambda s, k: None)
    monkeypatch.setattr(worker_main, "_install_signal_handlers", lambda loop, stop: None)
    monkeypatch.setattr(worker_main, "run_worker", run_worker)
    url = "postgresql+psycopg://u:p@127.0.0.1:1/x"
    try:
        await worker_main._run(make_settings(url, mowl_listing_lag_seconds=7))  # pyright: ignore[reportPrivateUsage]
        [(runtime, lag)] = seen
        assert runtime == saving.Runtime(ring)
        assert lag == 7
    finally:
        saving.configure(None)
        saving.configure_listing_lag(None)
        worker_main.design.configure(None)


async def test_the_saved_row_names_only_the_operations_own_class_and_revision(
    senv: SaveEnv,
) -> None:
    """``on_succeeded`` builds the row from the operation and a revision **of the operation's
    class**, joined on owner and class: a context naming another class's revision, another
    user's operation or a hash that is not the revision's writes nothing."""
    from classbeat.db import user_session
    from classbeat.operations import Operation

    saved = await senv.saved_class()
    second = senv.plant(saved.owner, saved.connection)  # same user, another class
    other = await senv.saved_class(TOKEN_B)

    def plant_op(owner: UUID, class_id: UUID, external_id: str) -> UUID:
        return senv.sql(
            "INSERT INTO external_operations (id, owner_id, kind, idempotency_key, request_hash, "
            "state, lease_version, class_id, external_id, request_context, pending_at) VALUES "
            "(:id, :o, 'mowl_save_workout', :k, :h, 'succeeded', 1, :c, :e, '{}'::jsonb, now()) "
            "RETURNING id",
            id=uuid.uuid4(), o=owner, k=f"{uuid.uuid4()}:save", h=b"\x01" * 32, c=class_id,
            e=external_id,
        ).scalar_one()  # fmt: skip

    def op(op_id: UUID, class_id: UUID, external_id: str, revision: Saved, **ctx: Any) -> Operation:
        context = operation.save_context(revision.revision_id, revision.hash_hex, 1001, [])
        return Operation(
            id=op_id, kind=operation.KIND, idempotency_key="k:save", state="succeeded",
            external_id=external_id, job_id=None, class_id=class_id, context={**context, **ctx},
        )  # fmt: skip

    async def write(o: Operation, owner: UUID = saved.owner) -> None:
        ctx = make_context(owner)
        async with user_session(senv.ops.factory, ctx) as db:
            await operation.SAVE.on_succeeded(db, owner, o)  # pyright: ignore[reportOptionalCall]

    mine = plant_op(saved.owner, saved.class_id, "700001")
    await write(op(mine, saved.class_id, "700001", second))  # another class's revision
    await write(op(mine, second.class_id, "700001", second))  # the row's class is not that
    await write(op(mine, saved.class_id, "700001", saved, content_hash="0" * 64))
    await write(op(mine, saved.class_id, "700002", saved))  # not the row's external id
    theirs = plant_op(other.owner, other.class_id, "700003")
    await write(op(theirs, other.class_id, "700003", other))  # under this user's context
    assert senv.sql("SELECT count(*) FROM saved_workouts").scalar_one() == 0
    await write(op(mine, saved.class_id, "700001", saved))
    await write(op(mine, saved.class_id, "700001", saved))  # idempotent
    [row] = senv.saved_rows(saved.owner)
    assert (row.class_id, row.revision_id, row.operation_id, row.program_id) == (
        saved.class_id, saved.revision_id, mine, 700001,
    )  # fmt: skip


async def test_admit_refuses_a_connection_that_is_not_the_contexts(senv: SaveEnv) -> None:
    """``admit`` compares the input's connection with the signed-in context itself (defence in
    depth next to the jobs API's generic check and ``connections.find_in``): a connected row
    of the user that the context does not name is refused."""
    from classbeat.db import user_session

    saved = await senv.saved_class()
    parsed = saving.SaveWorkoutInput.model_validate(saved.input())
    named = make_context(saved.owner, connections=[("mowl", saved.connection)])
    unnamed = make_context(saved.owner)  # the same user, the connection not in the context
    other = make_context(saved.owner, connections=[("mowl", uuid.uuid4())])
    for ctx, expected in ((unnamed, "not_connected"), (other, "not_connected"), (named, None)):
        async with user_session(senv.ops.factory, ctx) as db:
            result = await save_kind.admit(db, ctx, parsed)
        if expected is None:
            assert isinstance(result, save_kind.Admitted)
        else:
            assert isinstance(result, save_kind.Refused)
            assert result.reason == expected
