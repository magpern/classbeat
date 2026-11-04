"""Save and Update of a soundless class with no playlist (ADR 0007 amendment of 2026-11-03,
items 1, 2 and 8; SL3, #529), against Postgres as ``classbeat_app`` and the in-process fake MOWL
(``savetools``). Never the network, never a real account.

Covered: a soundless revision is written with ``PlaylistID: -1`` (the library's "no music") and
no link, attach or playlist read; the saved row's ``playlist_id`` is NULL; the request hash and
context carry ``-1`` while a playlist class's are byte for byte as before; the read-back of
``-1``, ``null`` or a missing ``PlaylistID`` verifies and any other value does not; the
uncertain-write path and both reconcilers; the revision's music and the class's ``music_source``
must agree (both ways, and a running class never saves ``none``); the soundless switch
(environment and stored value) at creation and in the worker, never read by a reconciler; every
existing ``state_hash`` unchanged. **What real MOWL returns for a program saved or updated with
``-1`` is unverified** (the owner's live checks): the fake serves each of the three forms, and an
Update that keeps a playlist attached in the app, so every case is decided by the same rules.
"""

import copy
import importlib.util
import json
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from classbeat import mowl_state
from classbeat.design import music_kind
from classbeat.operations import Operation, ReconcileInput, Reconciliation
from classbeat.operations.kinds import request_hash
from classbeat.saving import approval, operation, update_operation
from ctx import make_context
from optools import TOKEN_A
from savetools import (
    DRAFT,
    LINKED_PLAYLIST,
    NO_MUSIC,
    RUNNING_BRIEF,
    RUNNING_DRAFT,
    FakeMowlPrograms,
    Saved,
    SaveEnv,
    draft_hash,
    running_spec_of,
    spec_of,
)
from updatetools import DRAFT_2, Live, UpdateEnv

SPEC = spec_of(DRAFT)
SEGMENTS = len(DRAFT["segments"])
WRITES = 2 + 3 * SEGMENTS
NO_MUSIC_READS = ("-1", "null", "absent")
# ``mowl_state.state_hash`` of programs read with a positive or no ``PlaylistID``, computed by
# ``mowl_state`` on main before SL3: none of them may change (``saved_workouts.state_hash``).
GOLDEN = {
    ("DRAFT", 7): "e41c234a781dcaa42454f937f97faa7c5199c00dbf87fe1eab0ee7ff787f6d46",
    ("DRAFT", 1001): "d7e9fdf7c6dd7311f99bc03940c17417e432d87dfd39e5f3d67ebfc59de8ac79",
    ("DRAFT", None): "18e4523801f1d1fc1ff4ae922e31350d2394b3a52949e12663792467895b3980",
    ("DRAFT_2", 7): "c88baab1a6813f0d62502816e13533eaa2d4999db88d730c71387bf32109f709",
    ("DRAFT_2", 1001): "3417ac03ebb4d3d1150bf512d34978eacb9e91b06b3dea627d3c3fd89ff681be",
    ("DRAFT_2", None): "8eeee374b8cdfc5eeb6f7932903628bc36fc41a55f200d82230ad4505d2f70bf",
    ("RUN", 7): "bb803040d40cb24ef724317677e5c52484603a44e307b669415d7cbc72f246fc",
    ("RUN", 1001): "415412bc02709dcc821cb5696dee6bdfc8f6c0fbf0069b42733f0f6e97d9985d",
    ("RUN", None): "559c650c81eff4ae28ba74507c049b703299d75b6ce5bd8047df7e20680bdf88",
}
SPECS = {"DRAFT": SPEC, "DRAFT_2": spec_of(DRAFT_2), "RUN": running_spec_of(RUNNING_DRAFT)}


def gate(senv: SaveEnv, *, enabled: bool = True, stored: Any = "on") -> None:
    """The soundless switch: the environment's (``enabled``) and the stored admin flag (None: no
    row). Coverage is never consulted by Save, so the gate's ``fake_covers`` stays false."""
    music_kind.configure(music_kind.SoundlessGate(enabled=enabled, fake_covers=False))
    senv.sql("DELETE FROM app_settings WHERE key = 'soundless_enabled'")
    if stored is not None:
        senv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('soundless_enabled', CAST(:v AS jsonb))",
            v=json.dumps(stored),
        )


@pytest.fixture(autouse=True)
def soundless_on(senv: SaveEnv) -> Iterator[None]:
    gate(senv)
    try:
        yield
    finally:
        music_kind.configure(None)
        senv.sql("DELETE FROM app_settings WHERE key = 'soundless_enabled'")


def first_write(fake: FakeMowlPrograms, path: str = "/v1/programs") -> dict[str, Any]:
    """The header the library sent (the program as the fake keeps it)."""
    [program] = [p for p in fake.programs.values() if p.token == TOKEN_A]
    assert any(w.endswith(f" PUT {path}") for w in fake.writes)
    return program.body


def music_requests(fake: FakeMowlPrograms) -> list[str]:
    """Any request about music: an attach, a playlist list or read, an analysis, Swedebeat."""
    words = ("/v1/Spotify", "/v1/Playlists", "swedebeat", "AudioAnalysis")
    return [r for r in fake.requests if any(w.lower() in r.lower() for w in words)]


# --- the normal form ----------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "playlist"), list(GOLDEN))
def test_every_existing_state_hash_is_unchanged(name: str, playlist: int | None) -> None:
    spec = SPECS[name]
    fake = FakeMowlPrograms()
    read = fake.read_program(fake.programs[fake.plant("t", spec, playlist_id=playlist or 7)])
    if playlist is None:
        read["PlaylistID"] = None
    digest = mowl_state.state_hash(mowl_state.normalized_program(read)).hex()
    assert digest == GOLDEN[(name, playlist)]
    assert mowl_state.state_hash(mowl_state.normalized_spec(spec, playlist)).hex() == digest
    if playlist is None:
        del read["PlaylistID"]  # a missing field reads as null, as before
        assert mowl_state.state_hash(mowl_state.normalized_program(read)).hex() == digest


@pytest.mark.parametrize("shown", NO_MUSIC_READS)
def test_no_music_is_null_and_reads_of_minus_one_null_or_absent_match(shown: str) -> None:
    fake = FakeMowlPrograms(no_music_read=shown)
    read = fake.read_program(fake.programs[fake.plant("t", SPEC, playlist_id=-1)])
    assert ("PlaylistID" in read) is (shown != "absent")
    assert read["Playlist"] is None
    assert mowl_state.normalized_spec(SPEC, -1)["playlist_id"] is None
    assert mowl_state.normalized_program(read)["playlist_id"] is None
    assert mowl_state.matches(SPEC, read, playlist_id=-1)
    # The intended "no music" never matches a program on a playlist, nor a playlist class "none".
    assert not mowl_state.matches(SPEC, read, playlist_id=LINKED_PLAYLIST)


@pytest.mark.parametrize("value", [LINKED_PLAYLIST, 1, 0, -2, "7", 7.0])
def test_any_other_read_playlist_does_not_match_no_music(value: Any) -> None:
    fake = FakeMowlPrograms()
    read = fake.read_program(fake.programs[fake.plant("t", SPEC, playlist_id=-1)])
    read["PlaylistID"] = value
    assert not mowl_state.matches(SPEC, read, playlist_id=-1)


# --- the request hash, the context and the stored playlist ---------------------------------------


def test_the_request_hashes_and_contexts_of_a_playlist_class_are_unchanged() -> None:
    h = "ab" * 32
    rev = uuid.UUID(int=1)
    assert operation.save_hash(h, 7) == request_hash(
        operation.KIND, content_hash=h, playlist_id="7"
    )
    assert operation.save_hash(h, 7).hex() == (
        "e97c4c6f4bba1ede90cc777be08efac59d21efef7bf765607b11b1fa55760618"
    )
    assert operation.save_context(rev, h, 7, [3, 2]) == {
        "revision_id": str(rev), "content_hash": h, "playlist_id": 7, "baseline": [3, 2],
    }  # fmt: skip
    assert update_operation.update_hash(42, h, 7) == request_hash(
        update_operation.KIND, program_id="42", content_hash=h, playlist_id="7"
    )
    # A soundless write is another request: never mistaken for a playlist one on a retry.
    assert operation.save_hash(h, -1) != operation.save_hash(h, 7)
    assert update_operation.update_hash(42, h, -1) != update_operation.update_hash(42, h, 7)
    assert operation.save_context(rev, h, -1, [])["playlist_id"] == -1


def test_the_stored_playlist_is_null_for_no_music_and_never_anything_else() -> None:
    assert operation.stored_playlist(-1) is None
    assert operation.stored_playlist(LINKED_PLAYLIST) == LINKED_PLAYLIST
    for bad in (0, -2, True, False, None, "7", 7.0, 2**63):
        assert operation.stored_playlist(bad) is False


def _context(**over: Any) -> dict[str, Any]:
    base = update_operation.update_context(
        uuid.uuid4(), uuid.uuid4(), "ab" * 32, -1, 42, None, [42, 41]
    )
    return {**base, **over}


def test_known_accepts_minus_one_as_the_playlist_only() -> None:
    known = update_operation._known(_context())  # pyright: ignore[reportPrivateUsage]
    assert known == (42, -1, {42, 41}, None)
    for over in (
        {"program_id": -1},
        {"baseline": [42, -1]},
        {"playlist_id": 0},
        {"playlist_id": -2},
        {"playlist_id": True},
        {"playlist_id": "-1"},
    ):
        assert update_operation._known(_context(**over)) is None, over  # pyright: ignore[reportPrivateUsage]


# --- Save ------------------------------------------------------------------------------------


@pytest.mark.parametrize("shown", NO_MUSIC_READS)
async def test_a_soundless_class_is_saved_with_no_playlist(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any, shown: str
) -> None:
    fake_programs.no_music_read = shown
    saved = await senv.soundless_class()
    # The user's link row is never read: gone, the save still goes ahead with no music.
    senv.sql("DELETE FROM mowl_playlist_links WHERE id = :id", id=saved.link_id)
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    await senv.run_next()
    row = senv.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert senv.op_states(job_id) == ["pending", "succeeded"]

    header = first_write(fake_programs)
    assert header["PlaylistID"] == -1
    assert header["IsPublic"] is False
    assert len(fake_programs.writes) == WRITES  # the save's own requests only
    assert music_requests(fake_programs) == []

    [op] = senv.operations(saved.owner)
    assert op.request_context["playlist_id"] == -1
    assert bytes(op.request_hash) == operation.save_hash(saved.hash_hex, -1)
    [saved_row] = senv.saved_rows(saved.owner)
    assert saved_row.playlist_id is None  # NULL, never -1 (its CHECK is > 0)
    want = mowl_state.state_hash(mowl_state.normalized_spec(SPEC, -1))
    assert saved_row.state_hash is not None
    assert bytes(saved_row.state_hash) == want


@pytest.mark.parametrize("value", [LINKED_PLAYLIST, 0])
async def test_a_read_back_with_another_playlist_does_not_verify(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch, value: int
) -> None:
    saved = await senv.soundless_class()
    shown = fake_programs.read_program

    def with_playlist(program: Any) -> dict[str, Any]:
        return {**shown(program), "PlaylistID": value}

    monkeypatch.setattr(fake_programs, "read_program", with_playlist)
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.row(job_id).state == "completed"  # the write is final
    [saved_row] = senv.saved_rows(saved.owner)
    assert (saved_row.playlist_id, saved_row.state_hash) == (None, None)  # saved, not checked


async def test_a_lost_last_answer_is_uncertain_then_reconciled(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    fake_programs.no_music_read = "null"
    saved = await senv.soundless_class()
    fake_programs.fail = {WRITES: "drop"}
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.op_states(job_id) == ["pending", "uncertain", "succeeded"]
    [program] = fake_programs.owned(TOKEN_A)
    [op] = senv.operations(saved.owner)
    assert (op.state, op.resolved_by, op.external_id) == ("succeeded", "worker", str(program.id))
    [saved_row] = senv.saved_rows(saved.owner)
    assert (saved_row.program_id, saved_row.playlist_id) == (program.id, None)
    assert senv.row(job_id).state == "completed"


@pytest.mark.parametrize("number", [1, WRITES - 1])
async def test_a_failure_mid_write_is_uncertain_never_failed(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, number: int
) -> None:
    saved = await senv.soundless_class()
    fake_programs.fail = {number: "refuse"}
    job_id = senv.job(saved)
    await senv.run_next()
    states = senv.op_states(job_id)
    assert states[:2] == ["pending", "uncertain"]
    assert "failed" not in states
    assert senv.row(job_id).error_category == "uncertain_write"
    assert senv.saved_rows(saved.owner) == []


def save_op(saved: Saved, baseline: list[int], playlist_id: int = -1) -> Operation:
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
            **operation.save_context(saved.revision_id, saved.hash_hex, playlist_id, baseline),
            "bound_seconds": 360,
        },
        pending_at=now - timedelta(seconds=3660),
        call_ended_at=now - timedelta(seconds=3600),
    )


async def reconcile(senv: SaveEnv, owner: UUID, op: Operation, kind: Any) -> Reconciliation:
    check = ReconcileInput(senv.ops.factory, senv.ops.keyring, make_context(owner), op)
    return await kind.reconcile(check)


async def test_the_save_reconciler_needs_a_program_without_music(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.soundless_class()
    old = fake_programs.plant(TOKEN_A, name="Older")
    op = save_op(saved, [old])
    # The class's content on a playlist is not the effect of a soundless save.
    on_playlist = fake_programs.plant(TOKEN_A, SPEC, playlist_id=LINKED_PLAYLIST)
    assert await reconcile(senv, saved.owner, op, operation.SAVE) == Reconciliation("inconclusive")
    fake_programs.delete(on_playlist)
    made = fake_programs.plant(TOKEN_A, SPEC, playlist_id=-1)
    gate(senv, enabled=False, stored=None)  # the reconciler never reads the switch
    for shown in NO_MUSIC_READS:
        fake_programs.no_music_read = shown
        got = await reconcile(senv, saved.owner, op, operation.SAVE)
        assert got == Reconciliation("succeeded", str(made))
    # A playlist class's operation never takes the soundless program as its effect.
    assert await reconcile(
        senv, saved.owner, save_op(saved, [old], LINKED_PLAYLIST), operation.SAVE
    ) == Reconciliation("inconclusive")


# --- the music must agree, and a running class has music ----------------------------------------


@pytest.mark.parametrize(
    ("class_music", "revision_none", "brief"),
    [
        (None, True, None),  # a none revision on a class with no stored source
        ({"source": "mowl_spotify_playlist", "spotify_playlist_id": "x"}, True, None),
        (NO_MUSIC, False, None),  # a playlist revision on a soundless class
        (NO_MUSIC, True, RUNNING_BRIEF),  # a running class never saves "none"
    ],
)
async def test_revision_and_class_music_that_disagree_are_refused(
    senv: SaveEnv,
    fake_programs: FakeMowlPrograms,
    save_browser: Any,
    class_music: Any,
    revision_none: bool,
    brief: Any,
) -> None:
    owner, connection = await senv.owner()
    content = RUNNING_DRAFT if brief is not None else DRAFT
    saved = senv.plant(owner, connection, content, brief=brief)
    senv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :id",
        m=None if class_music is None else json.dumps(class_music), id=saved.class_id,
    )  # fmt: skip
    music = NO_MUSIC if revision_none else None
    revision = senv.plant_revision(owner, saved.class_id, saved.link_id, content, music=music)
    browser = await save_browser(saved)
    refused = await browser.save(revision_id=str(revision))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    job_id = senv.job(saved, revision_id=str(revision))
    await senv.run_next()
    assert (senv.row(job_id).state, senv.row(job_id).error_category) == ("failed", "invalid_input")
    assert (fake_programs.lists, fake_programs.write_count, fake_programs.programs) == (0, 0, {})


async def test_a_class_with_music_still_saves_its_linked_playlist(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    """A playlist class whose ``music_source`` is stored is saved as before, flag off or on."""
    gate(senv, enabled=False, stored=None)
    owner, connection = await senv.owner()
    saved = senv.plant(owner, connection)
    senv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :id",
        m=json.dumps({"source": "mowl_spotify_playlist", "spotify_playlist_id": "x"}),
        id=saved.class_id,
    )
    job_id = senv.job(saved)
    await senv.run_next()
    assert senv.row(job_id).state == "completed"
    assert first_write(fake_programs)["PlaylistID"] == LINKED_PLAYLIST
    [op] = senv.operations(owner)
    assert bytes(op.request_hash) == operation.save_hash(saved.hash_hex, LINKED_PLAYLIST)
    assert senv.saved_rows(owner)[0].playlist_id == LINKED_PLAYLIST


# --- the switch ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "stored"),
    [(False, "on"), (True, "off"), (True, None), (True, "maybe")],
)
async def test_save_and_update_are_feature_off_while_the_switch_is_off(
    uenv: UpdateEnv,
    fake_programs: FakeMowlPrograms,
    save_browser: Any,
    update_browser: Any,
    enabled: bool,
    stored: Any,
) -> None:
    live = await uenv.soundless_live()  # saved while on
    revision = uenv.revision(live, DRAFT_2, NO_MUSIC)
    other = await uenv.s.soundless_class()
    gate(uenv.s, enabled=enabled, stored=stored)
    writes, lists = fake_programs.write_count, fake_programs.lists
    refused = await (await save_browser(other)).save()
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    refused = await (await update_browser(live)).update(revision, DRAFT_2)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    # Queued while on, run while off: refused in ``prepare`` before the preflight.
    for job_id in (uenv.s.job(other), uenv.job(live, revision, DRAFT_2)):
        await uenv.run_next()
        assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == (
            "failed", "feature_off",
        )  # fmt: skip
    assert (fake_programs.write_count, fake_programs.lists) == (writes, lists)


# --- Update ----------------------------------------------------------------------------------


@pytest.mark.parametrize("shown", NO_MUSIC_READS)
async def test_a_soundless_class_is_updated_in_place_with_no_playlist(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any, shown: str
) -> None:
    fake_programs.no_music_read = shown
    live = await uenv.soundless_live()
    assert uenv.saved_row(live.saved_workout_id).playlist_id is None
    revision = uenv.revision(live, DRAFT_2, NO_MUSIC)
    uenv.sql("DELETE FROM mowl_playlist_links WHERE id = :id", id=live.saved.link_id)
    since = len(fake_programs.writes)
    answer = await (await update_browser(live)).update(revision, DRAFT_2)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == ("completed", None)
    assert fake_programs.writes[since].endswith(f"PUT /v1/programs/{live.program_id}")
    program = fake_programs.programs[live.program_id]
    assert program.body["PlaylistID"] == -1
    assert len(fake_programs.owned(TOKEN_A)) == 1
    assert music_requests(fake_programs) == []
    [op] = uenv.operations(live.saved.owner)
    assert op.request_context["playlist_id"] == -1
    want_hash = update_operation.update_hash(live.program_id, draft_hash(DRAFT_2).hex(), -1)
    assert bytes(op.request_hash) == want_hash
    row = uenv.saved_row(live.saved_workout_id)
    assert (row.revision_id, row.playlist_id) == (revision, None)
    want = mowl_state.state_hash(mowl_state.normalized_spec(spec_of(DRAFT_2), -1))
    assert row.state_hash is not None
    assert bytes(row.state_hash) == want


async def test_a_playlist_attached_in_the_app_is_program_changed_unless_overwrite(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    """The read first sees an attached playlist as a change. With ``overwrite``, and MOWL keeping
    the playlist (the unverified case), the read after the write does not match: the update is
    never recorded as verified, and the next Update reads it as changed again."""
    live = await uenv.soundless_live()
    revision = uenv.revision(live, DRAFT_2, NO_MUSIC)
    fake_programs.edit(live.program_id, PlaylistID=LINKED_PLAYLIST)
    writes = fake_programs.write_count
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert uenv.row(job_id).error_category == "program_changed"
    assert fake_programs.write_count == writes
    fake_programs.keep_playlist = True
    job_id = uenv.job(live, revision, DRAFT_2, overwrite=True)
    await uenv.run_next()
    assert uenv.row(job_id).state == "completed"
    assert fake_programs.programs[live.program_id].body["PlaylistID"] == LINKED_PLAYLIST
    row = uenv.saved_row(live.saved_workout_id)
    assert (row.revision_id, row.playlist_id, row.state_hash) == (revision, None, None)


async def test_a_kept_playlist_with_a_lost_answer_stays_uncertain(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.soundless_live()
    revision = uenv.revision(live, DRAFT_2, NO_MUSIC)
    fake_programs.edit(live.program_id, PlaylistID=LINKED_PLAYLIST)
    fake_programs.keep_playlist = True
    fake_programs.fail = {fake_programs.write_count + WRITES: "drop"}
    job_id = uenv.job(live, revision, DRAFT_2, overwrite=True)
    await uenv.run_next()
    assert uenv.op_states(job_id)[:2] == ["pending", "uncertain"]
    assert "succeeded" not in uenv.op_states(job_id)
    assert uenv.row(job_id).error_category == "uncertain_write"
    assert uenv.saved_row(live.saved_workout_id).revision_id == live.saved.revision_id


async def test_a_lost_update_answer_is_reconciled(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    fake_programs.no_music_read = "absent"
    live = await uenv.soundless_live()
    revision = uenv.revision(live, DRAFT_2, NO_MUSIC)
    fake_programs.fail = {fake_programs.write_count + WRITES: "drop"}
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert uenv.op_states(job_id) == ["pending", "uncertain", "succeeded"]
    row = uenv.saved_row(live.saved_workout_id)
    assert (row.revision_id, row.playlist_id) == (revision, None)


def update_op(uenv: UpdateEnv, live: Live, revision_id: UUID) -> Operation:
    program = uenv.fake.read_program(uenv.fake.programs[live.program_id])
    base = update_operation.update_context(
        live.saved_workout_id,
        revision_id,
        draft_hash(DRAFT_2).hex(),
        -1,
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
        context={**base, "bound_seconds": 360},
    )


async def test_the_update_reconciler_decides_a_soundless_write(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.soundless_live()
    revision = uenv.revision(live, DRAFT_2, NO_MUSIC)
    op = update_op(uenv, live, revision)
    owner = live.saved.owner
    gate(uenv.s, enabled=False, stored=None)  # never read by a reconciler
    # Unchanged, nothing new: provably no effect.
    assert await reconcile(uenv.s, owner, op, update_operation.UPDATE) == Reconciliation("failed")
    # Updated, but the playlist kept (the unverified case): inconclusive, never proof.
    made = fake_programs.programs.pop(
        fake_programs.plant(TOKEN_A, spec_of(DRAFT_2), playlist_id=-1)
    )
    target = fake_programs.programs[live.program_id]
    target.body, target.segment_ids = copy.deepcopy(made.body), made.segment_ids
    target.body["PlaylistID"] = LINKED_PLAYLIST
    got = await reconcile(uenv.s, owner, op, update_operation.UPDATE)
    assert got == Reconciliation("inconclusive")
    for shown in NO_MUSIC_READS:  # updated with no music, however MOWL shows it
        target.body["PlaylistID"] = -1
        fake_programs.no_music_read = shown
        got = await reconcile(uenv.s, owner, op, update_operation.UPDATE)
        assert got == Reconciliation("succeeded", str(live.program_id))


async def test_a_playlist_class_update_writes_its_playlist_as_before(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert uenv.row(job_id).state == "completed"
    assert fake_programs.programs[live.program_id].body["PlaylistID"] == LINKED_PLAYLIST
    [op] = uenv.operations(live.saved.owner)
    assert op.request_context["playlist_id"] == LINKED_PLAYLIST
    assert uenv.saved_row(live.saved_workout_id).playlist_id == LINKED_PLAYLIST


async def test_the_revision_check_names_no_link_for_a_soundless_revision(senv: SaveEnv) -> None:
    from classbeat.db import user_session

    saved = await senv.soundless_class()
    ctx = make_context(saved.owner)
    async with user_session(senv.ops.factory, ctx) as db:
        found = await approval.revision(
            db, ctx, saved.class_id, saved.revision_id, bytes.fromhex(saved.hash_hex)
        )
        assert isinstance(found, approval.Revision)
        assert (found.soundless, found.link_id, found.spec) == (True, None, SPEC)
        assert await approval.playlist_for(db, ctx, found) == approval.NO_PLAYLIST == -1


# --- the e2e fake MOWL serves the same cases -----------------------------------------------------


def _e2e_fake() -> Any:
    folder = Path(__file__).resolve().parents[2] / "e2e" / "fake-mowl"
    sys.path.insert(0, str(folder))  # its sibling ``swedebeat_mixes``
    try:
        spec = importlib.util.spec_from_file_location("e2e_fake_mowl", folder / "server.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(folder))
    return module


def test_the_e2e_fake_keeps_and_reads_a_program_without_music() -> None:
    fake: Any = _e2e_fake()
    created = fake.apply_write("tok", "PUT", "/v1/programs", {"Name": "Quiet", "PlaylistID": -1})
    program_id = created["ProgramID"]
    program = fake.PROGRAMS[program_id]
    for shown, expected in (
        ("-1", {"PlaylistID": -1}),
        ("null", {"PlaylistID": None}),
        ("absent", {}),
    ):
        fake.NO_MUSIC.update(read=shown, keep=False)
        read = fake.read_program(program_id, program)
        assert {k: v for k, v in read.items() if k == "PlaylistID"} == expected
        assert read["Playlist"] is None
    fake.NO_MUSIC.update(read="-1", keep=True)
    program["body"]["PlaylistID"] = 4242  # attached in the app
    fake.apply_write("tok", "PUT", f"/v1/programs/{program_id}", {"PlaylistID": -1})
    assert program["body"]["PlaylistID"] == 4242  # kept: the unverified case
    fake.NO_MUSIC.update(keep=False)
    fake.apply_write("tok", "PUT", f"/v1/programs/{program_id}", {"PlaylistID": -1})
    assert program["body"]["PlaylistID"] == -1
