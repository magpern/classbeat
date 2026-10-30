"""The ``mowl_save_workout`` reconciler (ADR 0007 section 8), row by row of its table: My Workouts'
first page now against the baseline the write stored, and the content of each new candidate. The
in-process fake MOWL (``savetools``); never the network.
"""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from classbeat import saving
from classbeat.operations import Operation, ReconcileInput, Reconciliation
from classbeat.saving import operation
from ctx import make_context
from optools import TOKEN_A, TOKEN_B
from savetools import DRAFT, LINKED_PLAYLIST, FakeMowlPrograms, Saved, SaveEnv, spec_of

SPEC = spec_of(DRAFT)


def save_operation(
    saved: Saved,
    baseline: list[int],
    *,
    ended_ago: float | None = 3600.0,
    playlist_id: int = LINKED_PLAYLIST,
    **context: Any,
) -> Operation:
    now = datetime.now(UTC)
    pending_at = now - timedelta(seconds=(ended_ago or 0) + 60)
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
            "bound_seconds": 180,
            **context,
        },
        pending_at=pending_at,
        call_ended_at=None if ended_ago is None else now - timedelta(seconds=ended_ago),
    )


async def reconcile(senv: SaveEnv, saved: Saved, op: Operation, keyring: Any = "default") -> Any:
    ring = senv.ops.keyring if keyring == "default" else keyring
    check = ReconcileInput(senv.ops.factory, ring, make_context(saved.owner), op)
    return await operation.SAVE.reconcile(check)


def plant_save(fake: FakeMowlPrograms, **kwargs: Any) -> int:
    return fake.plant(
        TOKEN_A, SPEC, playlist_id=kwargs.pop("playlist_id", LINKED_PLAYLIST), **kwargs
    )


async def test_exactly_one_new_match_succeeds_even_beside_other_new_programs(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    old = [fake_programs.plant(TOKEN_A, name=f"Old {i}") for i in range(2)]
    old.append(plant_save(fake_programs))  # an earlier save of the same revision: in the baseline
    fake_programs.plant(TOKEN_A, name="Made in the app meanwhile")
    found = plant_save(fake_programs)
    fake_programs.plant(TOKEN_A, name="Also made meanwhile")
    fake_programs.plant(TOKEN_B, SPEC, playlist_id=LINKED_PLAYLIST)  # another account's
    got = await reconcile(senv, saved, save_operation(saved, old))
    assert got == Reconciliation("succeeded", str(found))
    assert fake_programs.lists == 1
    assert fake_programs.gets == 1


async def test_a_partial_save_is_inconclusive(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    partial = copy.deepcopy(SPEC)
    partial["segments"] = partial["segments"][:1]  # the segment list was never completed
    fake_programs.plant(TOKEN_A, partial, playlist_id=LINKED_PLAYLIST)
    saving.configure_listing_lag(0.001)  # even with the lag: a new program is never "failed"
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE


async def test_no_new_program_is_inconclusive_unless_the_lag_rule_holds(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    old = plant_save(fake_programs)  # matches, but it was there before: not the effect
    baseline = [old]
    assert await reconcile(senv, saved, save_operation(saved, baseline)) == operation.INCONCLUSIVE
    saving.configure_listing_lag(60)
    failed = Reconciliation("failed")
    assert await reconcile(senv, saved, save_operation(saved, baseline, ended_ago=61)) == failed
    for op in (
        save_operation(saved, baseline, ended_ago=30),  # not long enough ago
        save_operation(saved, baseline, ended_ago=None),  # the call never returned
    ):
        assert await reconcile(senv, saved, op) == operation.INCONCLUSIVE
    # a returned call of an earlier attempt does not count: call_ended_at < pending_at
    earlier = save_operation(saved, baseline, ended_ago=600)
    stale = _with(earlier, pending_at=datetime.now(UTC) - timedelta(seconds=10))
    assert not stale.call_returned
    assert await reconcile(senv, saved, stale) == operation.INCONCLUSIVE


def _fields(op: Operation) -> dict[str, Any]:
    return {name: getattr(op, name) for name in Operation.__dataclass_fields__}


def _with(op: Operation, **changes: Any) -> Operation:
    return Operation(**{**_fields(op), **changes})


async def test_two_new_matches_are_inconclusive(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    plant_save(fake_programs)
    plant_save(fake_programs)
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE


async def test_a_listing_that_is_not_covered_is_inconclusive(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    baseline = [fake_programs.plant(TOKEN_A, name="Before")]
    plant_save(fake_programs)  # the target, now off page 0
    for i in range(operation.PAGE_SIZE):
        fake_programs.plant(TOKEN_A, name=f"New {i}")
    saving.configure_listing_lag(0.001)
    assert await reconcile(senv, saved, save_operation(saved, baseline)) == operation.INCONCLUSIVE
    # 50 new programs and nothing of the baseline on the page, even if one of them matches
    found = plant_save(fake_programs)
    assert await reconcile(senv, saved, save_operation(saved, baseline)) == operation.INCONCLUSIVE
    # mutation check: with the baseline program on the page the same match succeeds
    fresh = await senv.saved_class()
    fake_programs.programs = {
        k: v for k, v in fake_programs.programs.items() if k in (baseline[0], found)
    }
    got = await reconcile(senv, fresh, save_operation(fresh, baseline))
    assert got == Reconciliation("succeeded", str(found))


async def test_a_failed_listing_or_read_is_inconclusive(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    plant_save(fake_programs)
    saving.configure_listing_lag(0.001)
    fake_programs.list_status = 503
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE
    fake_programs.list_status = None
    fake_programs.get_status = 503
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE


@pytest.mark.parametrize("how", ["public", "other_playlist", "other_content"])
async def test_a_new_program_that_does_not_match_exactly_is_no_match(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, how: str
) -> None:
    saved = await senv.saved_class()
    if how == "public":
        plant_save(fake_programs, public=True)
    elif how == "other_playlist":
        plant_save(fake_programs, playlist_id=LINKED_PLAYLIST + 1)
    else:
        changed = copy.deepcopy(SPEC)
        changed["segments"][1]["intervals"][0]["zone"] = 3
        fake_programs.plant(TOKEN_A, changed, playlist_id=LINKED_PLAYLIST)
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE


async def test_more_candidates_than_reads_is_inconclusive(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    for _ in range(operation.MAX_PROGRAM_READS):
        fake_programs.plant(TOKEN_A, name=SPEC["name"])  # same name, other content
    found = plant_save(fake_programs)
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE
    assert fake_programs.gets == 0
    # one fewer same-named program: all are read and the one match is found
    fake_programs.programs.pop(min(fake_programs.programs))
    got = await reconcile(senv, saved, save_operation(saved, []))
    assert got == Reconciliation("succeeded", str(found))


async def test_without_the_revision_key_or_context_it_is_inconclusive(
    senv: SaveEnv, fake_programs: FakeMowlPrograms
) -> None:
    saved = await senv.saved_class()
    plant_save(fake_programs)
    good = save_operation(saved, [])
    assert (await reconcile(senv, saved, good)).outcome == "succeeded"
    for op in (
        _with(good, context={**good.context, "content_hash": "0" * 64}),
        _with(good, context={**good.context, "revision_id": str(uuid.uuid4())}),
        _with(good, context={**good.context, "baseline": "x"}),
        _with(good, context={**good.context, "playlist_id": True}),
        _with(good, class_id=None),
        _with(good, class_id=senv.plant(saved.owner, saved.connection).class_id),  # other class
    ):
        assert await reconcile(senv, saved, op) == operation.INCONCLUSIVE
    assert await reconcile(senv, saved, good, keyring=None) == operation.INCONCLUSIVE
    # another user's context can't read this user's revision (RLS and owner_id)
    other = await senv.saved_class(TOKEN_B)
    check = ReconcileInput(senv.ops.factory, senv.ops.keyring, make_context(other.owner), good)
    assert await operation.SAVE.reconcile(check) == operation.INCONCLUSIVE


def test_the_name_filter_survives_ellipsis_and_unicode_normalization() -> None:
    """A listing may cut a long name with ``…`` or ``...`` and may spell an accent composed or
    decomposed (or in a compatibility form); none of that rules a program out. A different text
    still does, and so does a prefix of another word's start."""
    name = "Tuesday climb \u00e9tude"  # composed é
    for listed in (
        "Tuesday cl…",
        "Tuesday cl...",
        "Tuesday climb \u00e9...",
        "Tuesday climb e\u0301tude",  # decomposed é
        "Tuesday climb e\u0301...",
        "TUESDAY  CLIMB \u00c9TUDE\u2026",
        "\uff34uesday climb \u00e9tude",  # fullwidth T
    ):
        assert operation.may_be_named(listed, name), listed
    assert operation.may_be_named("Caf\u00e9 ride", "Cafe\u0301 ride...")  # on the spec's side too
    # PR #172 review: NFKC doesn't keep prefixes, so a decomposed name cut after the base letter
    # only matches unnormalized; the filter matches if either key does.
    assert operation.may_be_named("Cafe", "Cafe\u0301 Climb")
    assert operation.may_be_named("Cafe\u0301 Climb", "Cafe")
    for listed in ("Wednesday cl…", "Tuesday hill...", "Made in the app…"):
        assert not operation.may_be_named(listed, name), listed


def test_the_name_filter_is_inclusive() -> None:
    name = "Tuesday climb"
    for listed in (
        None,
        "",
        "  ",
        7,
        "Tuesday climb",
        " tuesday  CLIMB ",
        "Tuesday cl",
        name + " (2)",
    ):
        assert operation.may_be_named(listed, name)
    for listed in ("Made in the app", "Wednesday climb", "Tuesday hill"):
        assert not operation.may_be_named(listed, name)


async def test_a_listing_name_shown_differently_still_reads_the_program(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two matching programs whose listing names differ in case and spacing from the saved name:
    both are read, so the second match is not hidden (inconclusive, never ``succeeded``)."""
    saved = await senv.saved_class()
    first, second = plant_save(fake_programs), plant_save(fake_programs)
    real = fake_programs._listing  # pyright: ignore[reportPrivateUsage]

    def shown_differently(req: Any) -> Any:
        answer = real(req)
        body = json.loads(answer.read())
        for item in body["Data"]:
            if item["ProgramID"] in (first, second):
                item["Name"] = "  " + item["Name"].upper()
        return fake_programs._answer(req, 200, body)  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(fake_programs, "_listing", shown_differently)
    assert await reconcile(senv, saved, save_operation(saved, [])) == operation.INCONCLUSIVE
    assert fake_programs.gets == 2
