"""Update never changes a program's sport (issue #485, ADR 0007 section 7, ADR 0018): the read
first compares the program's own ``ActivityTypeID`` with the class's sport and refuses
``program_other_sport`` when they differ or the program's sport cannot be told, before anything
is written and also with ``overwrite``. The in-process fake MOWL (``savetools``); never the
network.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from classbeat import mowl_state
from classbeat.jobs import CATEGORIES, RETRYABLE
from classbeat.saving import update as update_kind
from optools import TOKEN_A
from savetools import LINKED_PLAYLIST, FakeMowlPrograms, spec_of
from updatetools import DRAFT_2, UpdateEnv

# A program's own ActivityTypeID that a cycling class must never overwrite: running, a value MOWL
# did not list, the right number in the wrong type, or none at all (the fake leaves it out).
NOT_CYCLING: list[Any] = [1, 2, -1, 99, True, False, 0.0, "0", None]


def test_the_category_is_a_job_category_that_is_not_retried() -> None:
    assert "program_other_sport" in CATEGORIES
    assert "program_other_sport" not in RETRYABLE


@pytest.mark.parametrize(
    ("value", "sport"),
    [(0, "cycling"), (1, "running"), (2, None), (-1, None), (True, None), (False, None),
     (0.0, None), (1.0, None), ("0", None), ("cycling", None), (None, None)],
)  # fmt: skip
def test_the_program_sport_is_its_own_activity_type_exactly(value: Any, sport: str | None) -> None:
    program: dict[str, Any] = {"ProgramID": 7}
    if value is not None:
        program["ActivityTypeID"] = value
    assert mowl_state.program_sport(program) == sport
    assert mowl_state.program_sport([program]) is None


def test_the_class_sport_is_cycling_unless_its_spec_says_running() -> None:
    assert mowl_state.spec_sport(spec_of(DRAFT_2)) == "cycling"  # every stored class today
    assert mowl_state.spec_sport({"sport": "cycling"}) == "cycling"
    assert mowl_state.spec_sport({"sport": "running"}) == "running"
    for odd in ("swimming", "Running", "", 1, None, True):
        assert mowl_state.spec_sport({"sport": odd}) is None


def _plan(spec: dict[str, Any], program_id: int = 7) -> Any:
    target = SimpleNamespace(program_id=program_id, state_hash=None)
    return SimpleNamespace(revision=SimpleNamespace(spec=spec), target=target)


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize(
    ("class_spec", "activity"),
    [({}, 1), ({"sport": "running"}, 0), ({"sport": "running"}, None), ({"sport": "x"}, 0)],
)
def test_the_rule_refuses_a_mismatch_either_way_before_overwrite(
    class_spec: dict[str, Any], activity: Any, overwrite: bool
) -> None:
    """The reverse direction too (a running class onto a cycling program), which no stored class
    reaches before R1 (#463): the rule itself on a read program."""
    program: dict[str, Any] = {"ProgramID": 7, "Name": "x", "IsPublic": False, "Segments": []}
    if activity is not None:
        program["ActivityTypeID"] = activity
    _, refusal = update_kind._refusal(program, cast(Any, _plan(class_spec)), overwrite)  # pyright: ignore[reportPrivateUsage]
    assert refusal == "program_other_sport"
    # A matching sport gets past the sport check (overwrite then skips the comparison).
    program["ActivityTypeID"] = 1 if class_spec.get("sport") == "running" else 0
    if class_spec.get("sport") in (None, "cycling", "running"):
        _, refusal = update_kind._refusal(program, cast(Any, _plan(class_spec)), True)  # pyright: ignore[reportPrivateUsage]
        assert refusal is None


def test_a_missing_program_stays_missing_whatever_its_sport() -> None:
    other = {"ProgramID": 8, "ActivityTypeID": 1}
    assert update_kind._refusal(other, cast(Any, _plan({})), True) == (None, "program_missing")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("value", NOT_CYCLING)
async def test_a_program_of_another_or_unknown_sport_is_never_written(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, value: Any, overwrite: bool
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    fake_programs.set_activity_type(live.program_id, value)
    before = fake_programs.read_program(fake_programs.programs[live.program_id])
    writes, requests = fake_programs.write_count, len(fake_programs.requests)
    job_id = uenv.job(live, revision, DRAFT_2, overwrite=overwrite)
    await uenv.run_next()
    row = uenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "program_other_sport")  # not retried
    assert uenv.stages(job_id) == ["prepare", "read"]
    assert fake_programs.write_count == writes  # nothing written
    sent = fake_programs.requests[requests:]
    assert not [r for r in sent if r.split(" ", 1)[0] in ("PUT", "DELETE")]
    assert [r.split(" ", 1)[0] for r in sent].count("GET") == 1  # the read itself
    assert fake_programs.read_program(fake_programs.programs[live.program_id]) == before
    assert uenv.operations(live.saved.owner) == []
    assert uenv.saved_row(live.saved_workout_id).revision_id == live.saved.revision_id


async def test_a_cycling_program_still_updates_and_keeps_its_sport(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    fake_programs.set_activity_type(live.program_id, 0)  # what every saved program reads
    job_id = uenv.job(live, revision, DRAFT_2)
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == ("completed", None)
    read = fake_programs.read_program(fake_programs.programs[live.program_id])
    assert mowl_state.matches(spec_of(DRAFT_2), read, playlist_id=LINKED_PLAYLIST)
    assert read["ActivityTypeID"] == 0
    assert len(fake_programs.owned(TOKEN_A)) == 1


async def test_overwrite_after_program_changed_never_flips_the_sport(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms
) -> None:
    """The user renamed the program and it is a running one: the plain update is refused for
    the sport, not as "changed" (so the page offers no "Update anyway"), and so is the
    overwrite."""
    live = await uenv.live()
    revision = uenv.revision(live)
    fake_programs.edit(live.program_id, Name="My run")
    fake_programs.set_activity_type(live.program_id, 1)
    writes = fake_programs.write_count
    for overwrite in (False, True):
        job_id = uenv.job(live, revision, DRAFT_2, overwrite=overwrite)
        await uenv.run_next()
        assert uenv.row(job_id).error_category == "program_other_sport"
    assert fake_programs.write_count == writes
    assert fake_programs.programs[live.program_id].body["ActivityTypeID"] == 1
