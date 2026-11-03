"""The plan's gates fail closed (#496, second PR): jobs stored without gates, and every path that
makes a stored draft current or writes it to MOWL without a model run.

- A model job whose stored input has no ``plan_gates`` (queued before the gates existed, or a song
  pick created then and approved up to a week later) runs under the user's **current** plan:
  ``with_plan_run`` reads it in the worker before the kind calls anything, refuses the attempt
  ``invalid_input`` for a brief over it, and checks every draft against it. The approval of such a
  pick checks the brief against the current plan too (409 ``quota``).
- "Make current", Save and Update check the chosen draft against the current plan: a revision made
  before a downgrade or an admin's change, with a game, an FTP test or a length the plan no longer
  includes, is 409 ``quota`` and nothing changes.

The admin's changes are the superuser's ``UPDATE plans``; the seeds are restored after each test.
"""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat import design, saving
from classbeat.db import user_session
from classbeat.jobs.credits import QuotaRefused
from classbeat.quota.gates import content_seconds
from classbeat.saving import kind as save_kind
from classbeat.saving import update as update_kind
from ctx import make_context
from dbtools import migration_module
from revisetools import ReviseEnv
from savetools import SaveEnv
from songtools import Rider, SongEnv
from test_plan_gates_jobs import PRO_GATES, design_body, has_game, to_free
from test_plan_gates_jobs import scripted as scripted  # the game scripts' fixture
from updatetools import UpdateEnv

STRIP = "UPDATE jobs SET input = input - 'plan_gates' WHERE id = :j"


@pytest.fixture(autouse=True)
def restore_plans(admin: Connection) -> Iterator[None]:
    yield
    admin.execute(text("UPDATE plans SET races_allowed = true, ftp_tests_allowed = true"))
    admin.execute(text(migration_module("plan_gates").SEED))


def stored_input(renv: ReviseEnv, job_id: UUID) -> dict[str, Any]:
    return renv.denv.ops.row(job_id).input


# --- the worker: a job stored without gates ---------------------------------------------------


@pytest.mark.usefixtures("scripted")
async def test_a_job_without_gates_runs_under_the_plan_tightened_after_creation(
    renv: ReviseEnv,
) -> None:
    """Created on pro (games allowed), stored without gates, then the admin takes games off pro:
    the worker refuses the game (the script expects the refusal; without it the job fails)."""
    class_id, base = await renv.design()
    renv.use("revise_good_refused")
    made = await renv.ask(class_id, base, "Add a game, please.")
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    assert stored_input(renv, job_id)["plan_gates"] == PRO_GATES
    renv.sql(STRIP, j=job_id)
    renv.sql("UPDATE plans SET games_allowed = false WHERE id = 'pro'")
    await renv.run(design.REVISE_KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert not has_game(renv.revisions(class_id)[-1].content)
    assert "plan_gates" not in stored_input(renv, job_id)  # read per attempt, never stored


@pytest.mark.usefixtures("scripted")
async def test_a_job_without_gates_gets_what_the_current_plan_includes(renv: ReviseEnv) -> None:
    """The current plan, not a fixed strictest default: on pro the game is accepted."""
    class_id, base = await renv.design()
    renv.use("revise_good_accepted")
    made = await renv.ask(class_id, base, "Add a game, please.")
    job_id = UUID(made.json()["id"])
    renv.sql(STRIP, j=job_id)
    await renv.run(design.REVISE_KIND)
    assert renv.denv.ops.row(job_id).state == "completed"
    assert has_game(renv.revisions(class_id)[-1].content)


async def test_a_job_without_gates_whose_brief_is_over_the_current_plan_calls_nothing(
    renv: ReviseEnv, fake_mowl: Any
) -> None:
    renv.use("good_first_draft")
    made = await renv.browser.post(
        "/api/jobs", design_body(renv, length_min=45), {"Idempotency-Key": "a" * 32}
    )
    assert made.status_code == 201, made.text  # 45 minutes on pro
    job_id = UUID(made.json()["id"])
    renv.sql(STRIP, j=job_id)
    to_free(renv)  # 30 minutes now
    renv.providers.clear()
    reads = len(fake_mowl.requests)
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert renv.providers == []  # no model was built
    assert len(fake_mowl.requests) == reads  # and MOWL was not called
    assert renv.denv.revisions(job_id) == []


# --- the approval of a pick stored without gates ----------------------------------------------


def length_refusal(limit: int, used: int) -> dict[str, Any]:
    detail = {"reason": "quota", "quota": "length", "limit": limit, "used": used, "plan": "pro"}
    return {"detail": detail}


async def waiting_without_gates(songenv: SongEnv) -> tuple[Rider, UUID, UUID, str]:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    assert songenv.row(job_id).input["plan_gates"]["max_class_minutes"] == 90
    songenv.sql(STRIP, j=job_id)
    return rider, job_id, revision, digest


async def test_an_admin_tightening_before_the_approval_refuses_a_pick_without_gates(
    songenv: SongEnv,
) -> None:
    rider, job_id, revision, digest = await waiting_without_gates(songenv)
    songenv.sql("UPDATE plans SET max_class_minutes = 20 WHERE id = 'pro'")  # the brief is 30
    before = (songenv.kinds(job_id), songenv.counts())
    answer = await rider.approve(job_id, revision, digest)
    assert (answer.status_code, answer.json()) == (409, length_refusal(20, 30))
    row = songenv.row(job_id)
    assert (row.state, row.approved_at) == ("waiting_for_approval", None)
    assert (songenv.kinds(job_id), songenv.counts()) == before
    songenv.sql("UPDATE plans SET max_class_minutes = 30 WHERE id = 'pro'")
    assert (await rider.approve(job_id, revision, digest)).status_code == 200


async def test_a_pick_with_gates_keeps_the_ones_it_was_admitted_with(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision, digest = await songenv.waiting(rider)
    songenv.sql("UPDATE plans SET max_class_minutes = 20 WHERE id = 'pro'")
    assert (await rider.approve(job_id, revision, digest)).status_code == 200


async def test_the_approved_phase_of_a_pick_without_gates_reads_the_plan_when_it_runs(
    songenv: SongEnv,
) -> None:
    """Approved under 90 minutes, tightened to 20 before phase 2 runs: it fails closed before
    any Spotify, MOWL or model call."""
    rider, job_id, revision, digest = await waiting_without_gates(songenv)
    assert (await rider.approve(job_id, revision, digest)).status_code == 200
    songenv.sql("UPDATE plans SET max_class_minutes = 20 WHERE id = 'pro'")
    before = songenv.counts()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert songenv.counts() == before


# --- Make current -----------------------------------------------------------------------------


async def pick(renv: ReviseEnv, class_id: UUID, revision: Any) -> Any:
    body = {"revision_id": str(revision.id), "content_hash": bytes(revision.content_hash).hex()}
    return await renv.browser.post(f"/api/classes/{class_id}/current", body)


async def test_make_current_refuses_an_older_revision_longer_than_the_plan_now(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    await renv.revise(class_id, base, "revise_good")
    first, second = renv.revisions(class_id)
    assert renv.current(class_id) == second.id
    renv.sql("UPDATE plans SET max_class_minutes = 10 WHERE id = 'pro'")  # a downgrade
    used = -(-content_seconds(first.content) // 60)
    assert used > 10
    refused = await pick(renv, class_id, first)
    assert (refused.status_code, refused.json()) == (409, length_refusal(10, used))
    assert renv.current(class_id) == second.id  # nothing changed
    renv.sql("UPDATE plans SET max_class_minutes = 90 WHERE id = 'pro'")
    assert (await pick(renv, class_id, first)).status_code == 200
    assert renv.current(class_id) == first.id


@pytest.mark.usefixtures("scripted")
async def test_make_current_refuses_an_older_revision_with_a_game_the_plan_lacks_now(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design("good_first_draft_accepted")  # a game, on pro
    await renv.revise(class_id, base, "revise_good")
    first, second = renv.revisions(class_id)
    assert has_game(first.content)
    assert not has_game(second.content)
    renv.sql("UPDATE plans SET games_allowed = false WHERE id = 'pro'")
    refused = await pick(renv, class_id, first)
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        {"reason": "quota", "quota": "feature", "feature": "games", "plan": "pro"},
    )
    assert renv.current(class_id) == second.id


# --- Save and Update --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ("games_allowed = false", {"quota": "feature", "feature": "games", "plan": "pro"}),
        ("max_class_minutes = 10", {"quota": "length", "limit": 10, "used": 15, "plan": "pro"}),
    ],
    ids=["game", "length"],
)
async def test_save_and_update_check_the_revision_against_the_current_plan(
    uenv: UpdateEnv, senv: SaveEnv, change: str, detail: dict[str, Any]
) -> None:
    """``DRAFT`` (15 minutes, one game) was fine on pro; after the admin's change neither Save nor
    Update admits it (the program already in MOWL is not touched)."""
    live = await uenv.live()
    saved = live.saved
    assert content_seconds(saved.content) == 900
    ctx = make_context(saved.owner, connections=[("mowl", saved.connection)])
    save_in = saving.SaveWorkoutInput.model_validate(saved.input())
    update_in = saving.UpdateWorkoutInput.model_validate(live.input())
    async with user_session(senv.ops.factory, ctx) as db:  # allowed before the change
        assert isinstance(await save_kind.admit(db, ctx, save_in), save_kind.Admitted)
        assert isinstance(await update_kind.admit(db, ctx, update_in), update_kind.Admitted)
    senv.sql(f"UPDATE plans SET {change} WHERE id = 'pro'")  # noqa: S608 - constants only
    for admit, parsed in ((save_kind.admit, save_in), (update_kind.admit, update_in)):
        async with user_session(senv.ops.factory, ctx) as db:
            with pytest.raises(QuotaRefused) as refused:
                await admit(db, ctx, parsed)
        assert refused.value.detail() == detail
