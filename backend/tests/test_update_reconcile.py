"""The ``mowl_update_workout`` reconciler and ``on_succeeded`` (ADR 0007 sections 7 and 8), row by
row: the target read now against the intended spec and the stored ``before_hash``, My Workouts'
first page against the baseline; and the saved row moved only for the operation's own class.
The in-process fake MOWL (``savetools``); never the network.
"""

import copy
import uuid
from typing import Any
from uuid import UUID

import pytest

from classbeat import mowl_state
from classbeat.db import user_session
from classbeat.operations import Operation, ReconcileInput, Reconciliation
from classbeat.saving import operation, update_operation
from ctx import make_context
from optools import TOKEN_A, TOKEN_B
from savetools import LINKED_PLAYLIST, FakeMowlPrograms, draft_hash, spec_of
from updatetools import DRAFT_2, Live, UpdateEnv

FAILED = Reconciliation("failed")


def update_op(
    uenv: UpdateEnv,
    live: Live,
    revision_id: UUID,
    *,
    content: dict[str, Any] = DRAFT_2,
    baseline: list[int] | None = None,
    before_hash: str | None = "current",
    **context: Any,
) -> Operation:
    if before_hash == "current":
        program = uenv.fake.read_program(uenv.fake.programs[live.program_id])
        before_hash = update_operation.state_hex(program)
    base = update_operation.update_context(
        live.saved_workout_id,
        revision_id,
        draft_hash(content).hex(),
        LINKED_PLAYLIST,
        live.program_id,
        before_hash,
        [live.program_id] if baseline is None else baseline,
    )
    return Operation(
        id=uuid.uuid4(),
        kind=update_operation.KIND,
        idempotency_key=f"{uuid.uuid4()}:update",
        state="uncertain",
        external_id=None,
        job_id=None,
        class_id=live.saved.class_id,
        context={**base, "bound_seconds": 180, **context},
    )


async def reconcile(uenv: UpdateEnv, live: Live, op: Operation, keyring: Any = "default") -> Any:
    ring = uenv.s.ops.keyring if keyring == "default" else keyring
    check = ReconcileInput(uenv.s.ops.factory, ring, make_context(live.saved.owner), op)
    return await update_operation.UPDATE.reconcile(check)


def apply(fake: FakeMowlPrograms, live: Live, content: dict[str, Any]) -> None:
    """Play the whole update in the fake: the program now holds ``content``."""
    spec = spec_of(content)
    fresh = fake.plant(TOKEN_A, spec, playlist_id=fake.programs[live.program_id].body["PlaylistID"])
    made = fake.programs.pop(fresh)
    target = fake.programs[live.program_id]
    target.body, target.segment_ids = made.body, made.segment_ids


async def test_a_target_that_matches_succeeds_even_beside_a_new_program(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    op = update_op(uenv, live, revision)
    apply(fake_programs, live, DRAFT_2)
    fake_programs.plant(TOKEN_A, name="Made in the app meanwhile")
    got = await reconcile(uenv, live, op)
    assert got == Reconciliation("succeeded", str(live.program_id))
    assert fake_programs.lists == 1  # the Save's preflight only: no listing was needed


async def test_an_unchanged_target_with_no_new_program_is_failed(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    old = fake_programs.plant(TOKEN_A, name="Older, still there")
    baseline = sorted([live.program_id, old], reverse=True)
    assert await reconcile(uenv, live, update_op(uenv, live, revision, baseline=baseline)) == FAILED


@pytest.mark.parametrize("how", ["new_program", "not_covered", "listing_fails", "malformed"])
async def test_an_unchanged_target_without_listing_proof_is_inconclusive(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    op = update_op(uenv, live, revision)
    if how == "new_program":  # MOWL made a copy (or anything new appeared)
        fake_programs.plant(TOKEN_A, name=DRAFT_2["name"])
    elif how == "not_covered":
        for i in range(operation.PAGE_SIZE):
            fake_programs.plant(TOKEN_A, name=f"New {i}")
    elif how == "listing_fails":
        fake_programs.list_status = 503
    else:
        monkeypatch.setattr(update_operation, "listed_ids", lambda listing: None)
    assert await reconcile(uenv, live, op) == operation.INCONCLUSIVE


async def test_a_baseline_program_on_a_full_page_still_covers_it(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    """Mutation check of the coverage rule: exactly 50 programs, all from the baseline."""
    live = await uenv.live()
    revision = uenv.revision(live)
    older = [fake_programs.plant(TOKEN_A, name=f"Old {i}") for i in range(operation.PAGE_SIZE - 1)]
    baseline = sorted([live.program_id, *older], reverse=True)
    assert await reconcile(uenv, live, update_op(uenv, live, revision, baseline=baseline)) == FAILED


@pytest.mark.parametrize("how", ["partial", "gone", "read_fails", "other_account", "edited"])
async def test_a_target_neither_intended_nor_unchanged_is_inconclusive(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, how: str
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    op = update_op(uenv, live, revision)
    if how == "partial":  # the header written, the segment list not
        fake_programs.edit(live.program_id, Name="Tuesday climb, harder")
    elif how == "gone":
        fake_programs.delete(live.program_id)
    elif how == "read_fails":
        fake_programs.get_status = 503
    elif how == "other_account":
        fake_programs.programs[live.program_id].token = TOKEN_B
    else:
        fake_programs.edit_interval(live.program_id, zone=1)
    assert await reconcile(uenv, live, op) == operation.INCONCLUSIVE
    assert fake_programs.lists == 1  # no listing read: the target already rules out proof


async def test_without_a_before_hash_or_a_readable_context_it_is_inconclusive(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    good = update_op(uenv, live, revision)
    assert await reconcile(uenv, live, good) == FAILED
    sibling = await uenv.sibling(live)

    def with_(**changes: Any) -> Operation:
        fields = {n: getattr(good, n) for n in Operation.__dataclass_fields__}
        return Operation(**{**fields, **changes})

    for op in (
        update_op(uenv, live, revision, before_hash=None),
        with_(context={**good.context, "before_hash": 7}),
        with_(context={**good.context, "program_id": True}),
        with_(context={**good.context, "program_id": str(live.program_id)}),
        with_(context={**good.context, "playlist_id": None}),
        with_(context={**good.context, "baseline": "x"}),
        with_(context={**good.context, "baseline": [True]}),
        with_(context={**good.context, "content_hash": "0" * 64}),
        with_(context={**good.context, "revision_id": str(uuid.uuid4())}),
        with_(class_id=None),
        with_(class_id=sibling.saved.class_id),  # the revision is not that class's
    ):
        assert await reconcile(uenv, live, op) == operation.INCONCLUSIVE
    assert await reconcile(uenv, live, good, keyring=None) == operation.INCONCLUSIVE
    other = await uenv.live(TOKEN_B)  # another user's context can't read this user's revision
    check = ReconcileInput(
        uenv.s.ops.factory, uenv.s.ops.keyring, make_context(other.saved.owner), good
    )
    assert await update_operation.UPDATE.reconcile(check) == operation.INCONCLUSIVE


# --- on_succeeded -------------------------------------------------------------------------------


def plant_op(uenv: UpdateEnv, owner: UUID, class_id: UUID, external_id: str) -> UUID:
    return uenv.sql(
        "INSERT INTO external_operations (id, owner_id, kind, idempotency_key, request_hash, "
        "state, lease_version, class_id, external_id, request_context, pending_at) VALUES "
        "(:id, :o, 'mowl_update_workout', :k, :h, 'succeeded', 1, :c, :e, '{}'::jsonb, now()) "
        "RETURNING id",
        id=uuid.uuid4(), o=owner, k=f"{uuid.uuid4()}:update", h=b"\x01" * 32, c=class_id,
        e=external_id,
    ).scalar_one()  # fmt: skip


async def write(uenv: UpdateEnv, owner: UUID, op: Operation) -> None:
    ctx = make_context(owner)
    async with user_session(uenv.s.ops.factory, ctx) as db:
        await update_operation.UPDATE.on_succeeded(db, owner, op)  # pyright: ignore[reportOptionalCall]


async def test_on_succeeded_moves_only_the_live_row_of_the_operations_own_class(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    sibling = await uenv.sibling(live)
    other = await uenv.live(TOKEN_B)
    revision = uenv.revision(live)
    sibling_revision = uenv.revision(sibling)
    before = uenv.saved_row(live.saved_workout_id)
    mine = plant_op(uenv, live.saved.owner, live.saved.class_id, str(live.program_id))

    def op(op_id: UUID, class_id: UUID, external_id: str, **ctx: Any) -> Operation:
        good = update_op(uenv, live, revision)
        return Operation(
            id=op_id, kind=update_operation.KIND, idempotency_key="k:update", state="succeeded",
            external_id=external_id, job_id=None, class_id=class_id,
            context={**good.context, **ctx},
        )  # fmt: skip

    # none of these may touch any saved row
    await write(uenv, live.saved.owner, op(mine, live.saved.class_id, str(live.program_id + 1)))
    await write(
        uenv, live.saved.owner,
        op(mine, live.saved.class_id, str(live.program_id), revision_id=str(sibling_revision)),
    )  # fmt: skip
    await write(
        uenv, live.saved.owner,
        op(mine, live.saved.class_id, str(live.program_id), content_hash="0" * 64),
    )  # fmt: skip
    # the sibling's saved row named in the context: not the operation's class
    await write(
        uenv, live.saved.owner,
        op(mine, live.saved.class_id, str(live.program_id),
           saved_workout_id=str(sibling.saved_workout_id)),
    )  # fmt: skip
    # an operation of the sibling class naming this class's row
    theirs_class = plant_op(uenv, live.saved.owner, sibling.saved.class_id, str(live.program_id))
    await write(
        uenv, live.saved.owner, op(theirs_class, sibling.saved.class_id, str(live.program_id))
    )
    # an operation of the sibling class naming this class's row, with a revision of its own class
    # (only the saved row's class join refuses it; the revision foreign key would refuse it too)
    await write(
        uenv, live.saved.owner,
        op(theirs_class, sibling.saved.class_id, str(live.program_id),
           revision_id=str(sibling_revision)),
    )  # fmt: skip
    # another user's operation, under this user's context
    foreign = plant_op(uenv, other.saved.owner, other.saved.class_id, str(other.program_id))
    await write(uenv, live.saved.owner, op(foreign, live.saved.class_id, str(live.program_id)))
    # the right operation, but the row is detached
    uenv.sql(
        "UPDATE saved_workouts SET detached_at = now() WHERE id = :id", id=live.saved_workout_id
    )
    await write(uenv, live.saved.owner, op(mine, live.saved.class_id, str(live.program_id)))
    uenv.sql(
        "UPDATE saved_workouts SET detached_at = NULL WHERE id = :id", id=live.saved_workout_id
    )
    for row_id, was in (
        (live.saved_workout_id, before),
        (sibling.saved_workout_id, None),
    ):
        now = uenv.saved_row(row_id)
        assert now.revision_id != revision
        if was is not None:
            assert (now.revision_id, bytes(now.state_hash)) == (
                was.revision_id, bytes(was.state_hash),
            )  # fmt: skip

    await write(uenv, live.saved.owner, op(mine, live.saved.class_id, str(live.program_id)))
    await write(uenv, live.saved.owner, op(mine, live.saved.class_id, str(live.program_id)))
    row = uenv.saved_row(live.saved_workout_id)
    assert (row.revision_id, row.revision_number, row.state_hash) == (revision, 2, None)
    assert bytes(row.content_hash) == draft_hash(DRAFT_2)
    assert row.operation_id == before.operation_id  # still the creating operation
    assert row.program_id == live.program_id


def test_the_state_hash_is_mowl_states_and_none_for_an_unreadable_program() -> None:
    assert update_operation.state_hex({"Name": "x"}) is None
    assert update_operation.state_hex(None) is None
    fake = FakeMowlPrograms()
    program = fake.programs[fake.plant(TOKEN_A, spec_of(DRAFT_2), playlist_id=LINKED_PLAYLIST)]
    read = fake.read_program(program)
    expected = mowl_state.state_hash(mowl_state.normalized_spec(spec_of(DRAFT_2), LINKED_PLAYLIST))
    assert update_operation.state_hex(read) == expected.hex()
    changed = copy.deepcopy(read)
    changed["IsPublic"] = True
    assert update_operation.state_hex(changed) != expected.hex()
