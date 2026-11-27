"""The composer's sketch no longer depends on the soundless switch (ADR 0038 amendment of
2026-11-24, #1484), against Postgres, the fake MOWL and ``FakeProvider``: the sketch's design,
its chat edit and ``can_change`` need only the job's own plan's coverage; the composer is offered
per plan (``composer_available``); the hand-off records its route; a ride in progress follows its
class. A real no-music class has no gate at all since #1543 (``test_soundless_jobs``); the sketch
keeps this one, also in the worker. Never the network, a real account or a model."""

from typing import Any
from uuid import UUID

import pytest

from classbeat.db import user_session
from classbeat.design import DESIGN_DRAFT, music_kind
from classbeat.design.kind import DesignDraftInput
from classbeat.jobs.kinds import Admitted, Refused
from ctx import make_context
from designtools import BRIEF
from sketchtools import NO_MUSIC, SketchEnv
from test_soundless_jobs import own_key, plan_keys, route

pytestmark = pytest.mark.usefixtures("jenv")


def gate(*, fake: bool) -> None:
    music_kind.configure(music_kind.SoundlessGate(fake_covers=fake))


async def answers(skenv: SketchEnv, class_id: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    browser = skenv.renv.browser
    detail = (await browser.get(f"/api/classes/{class_id}")).json()
    history = (await browser.get(f"/api/classes/{class_id}/history")).json()
    return detail, history


async def connections(skenv: SketchEnv) -> dict[str, Any]:
    got = await skenv.renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    return got.json()


# --- 1. the plan covered (no soundless switch since #1543) ------------------------------------


async def test_a_sketch_is_designed_and_edited_on_the_plans_coverage_alone(
    skenv: SketchEnv,
) -> None:
    gate(fake=True)
    flags = await connections(skenv)
    assert flags["composer_flow"] is True
    assert "soundless_enabled" not in flags  # retired (#1543)
    class_id, sketch = await skenv.sketched()
    assert skenv.ienv.interpretation(class_id)["route"] == "sketch"
    detail, history = await answers(skenv, class_id)
    assert (detail["composer"], detail["can_change"], history["can_change"]) == (True, True, True)
    job_id, _ = await skenv.renv.revise(class_id, sketch, "soundless_revise_good", "Seven bursts")
    row = skenv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    new = skenv.renv.current(class_id)
    assert new is not None
    assert new != sketch
    assert skenv.revision(new).music == {"source": "sketch"}
    detail, history = await answers(skenv, class_id)
    assert (detail["can_change"], history["can_change"]) == (True, True)


# --- 2. the plan not covered: the older route -------------------------------------------------


async def test_a_plan_without_coverage_is_read_without_the_composer_and_takes_the_older_route(
    skenv: SketchEnv,
) -> None:
    gate(fake=False)  # nothing covers soundless
    assert (await connections(skenv))["composer_flow"] is False
    class_id, interpret_job = await skenv.ienv.interpreted()
    assert skenv.ienv.stored_input(interpret_job).get("composer") is False
    made = await skenv.ienv.handoff(class_id)
    assert made.status_code == 201, made.text
    [job] = skenv.ienv.design_jobs(class_id)
    assert job.kind == "design_draft"
    assert skenv.ienv.stored_input(job.id).get("mode") is None
    detail, _ = await answers(skenv, class_id)
    assert detail["composer"] is False
    assert skenv.ienv.interpretation(class_id)["route"] == "classic"


async def test_a_class_read_as_a_composer_class_falls_back_when_coverage_lapses_before_hand_off(
    skenv: SketchEnv,
) -> None:
    gate(fake=True)
    class_id = await skenv.interpreted()
    gate(fake=False)  # the plan's route is cleared before the hand-off
    made = await skenv.ienv.handoff(class_id)
    assert made.status_code == 201, made.text  # no ``feature_off``, no ``soundless_not_evaluated``
    [job] = skenv.ienv.design_jobs(class_id)
    assert (job.kind, skenv.ienv.stored_input(job.id).get("mode")) == ("design_draft", None)
    assert skenv.ienv.interpretation(class_id)["route"] == "classic"
    detail, _ = await answers(skenv, class_id)
    assert detail["composer"] is False  # a class that fell back is an ordinary class
    gate(fake=True)
    again = await skenv.ienv.handoff(class_id)  # a reopened tab, with coverage back: same job
    assert (again.status_code, again.json()["job_id"]) == (200, str(job.id))
    assert skenv.ienv.interpretation(class_id)["route"] == "classic"  # the route never moves


# --- 3. two plans, one covered ----------------------------------------------------------------


async def test_the_composer_is_offered_per_plan_not_per_every_plan(skenv: SketchEnv) -> None:
    gate(fake=False)
    mine = await own_key(skenv.renv)
    other = next(k for k in plan_keys(skenv.renv) if k != mine)
    route(skenv.renv, mine)  # only the user's own plan is covered
    flags = await connections(skenv)
    assert flags["composer_flow"] is True
    assert "soundless_enabled" not in flags  # retired (#1543)
    covered = await skenv.interpreted()
    made = await skenv.ienv.handoff(covered)
    assert made.status_code == 201, made.text
    [job] = skenv.ienv.design_jobs(covered)
    assert skenv.ienv.stored_input(job.id).get("mode") == "sketch"
    # The user's plan without a route, another plan's covered: the plan counts, not the others.
    skenv.sql("DELETE FROM model_routes WHERE route_key = :k", k=mine)
    route(skenv.renv, other)
    assert (await connections(skenv))["composer_flow"] is False
    plain = await skenv.interpreted()
    made = await skenv.ienv.handoff(plain)
    assert made.status_code == 201, made.text
    [job] = skenv.ienv.design_jobs(plain)
    assert (job.kind, skenv.ienv.stored_input(job.id).get("mode")) == ("design_draft", None)


# --- 4. coverage removed after a sketch exists ------------------------------------------------


async def test_a_ride_in_progress_follows_its_class_when_coverage_lapses(
    skenv: SketchEnv,
) -> None:
    class_id, sketch = await skenv.sketched()
    gate(fake=False)  # coverage removed
    detail, history = await answers(skenv, class_id)
    assert detail["composer"] is True  # the class's route, not the current flag
    assert (detail["can_change"], history["can_change"]) == (False, False)
    refused = await skenv.renv.ask(class_id, sketch, "Easier start")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "soundless_not_evaluated"
    assert len(skenv.renv.messages(class_id)) == 1  # nothing written
    assert (await connections(skenv))["composer_flow"] is False  # a new class: the older route


# --- 4b. the design admit (a direct ``design_draft`` in mode ``sketch``) ----------------------


async def test_a_direct_sketch_design_on_an_uncovered_plan_is_refused_before_any_job(
    skenv: SketchEnv,
) -> None:
    """``kind_music.admit_music_choice`` runs ``sketch_gate.admit_sketch`` for a ``design_draft``
    in mode ``sketch``. A browser cannot send ``mode`` (422), so the hand-off is the only caller:
    its own admit is called here as the jobs API calls it. Without that call the job would be
    queued and only the worker's ``require_covered`` would stop it."""
    class_id = await skenv.interpreted()

    def sketch(mode: str | None) -> DesignDraftInput:
        return DesignDraftInput.model_validate(
            {
                "class_id": class_id,
                "brief": BRIEF,
                "music": NO_MUSIC,
                "mowl_connection_id": skenv.renv.browser.connection_id,
                "mode": mode,
            }
        )

    ctx = make_context(skenv.renv.owner)
    kind_admit = DESIGN_DRAFT.admit
    assert kind_admit is not None

    async def admit(mode: str | None) -> Refused | Admitted:
        async with user_session(skenv.renv.denv.ops.factory, ctx) as db:
            return await kind_admit(db, ctx, sketch(mode))

    before = skenv.ienv.counts()
    gate(fake=False)  # the user's own plan has no route covering soundless
    assert await admit("sketch") == Refused("soundless_not_evaluated")
    assert isinstance(await admit(None), Admitted)  # a real no-music class has no such gate
    assert skenv.ienv.counts() == before  # no class, no job, no credit
    gate(fake=True)
    assert isinstance(await admit("sketch"), Admitted)  # the same input, the plan covered


# --- 5. the worker keeps the sketch's rule (#1543) --------------------------------------------


async def test_a_sketch_on_a_model_not_covering_soundless_still_ends_model_config(
    skenv: SketchEnv,
) -> None:
    """#1543 serves a real no-music class on an uncovered model with the stale-route warning; a
    sketch keeps ``require_covered`` (``music_kind.serve_model`` with ``sketch``): ``model_config``
    before anything is sent, and the class gets no revision."""
    class_id = await skenv.interpreted()
    made = await skenv.ienv.handoff(class_id)
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["job_id"])
    assert skenv.ienv.stored_input(job_id).get("mode") == "sketch"
    skenv.renv.soundless_covered = False
    try:
        skenv.renv.use("soundless_first_draft")
        await skenv.renv.run("design_draft")
    finally:
        skenv.renv.soundless_covered = True
    row = skenv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert all(p.sent == [] for p in skenv.renv.providers)
    assert skenv.renv.current(class_id) is None


async def test_a_sketch_edit_on_a_model_not_covering_soundless_ends_model_config(
    skenv: SketchEnv,
) -> None:
    """``revise_soundless.run_soundless`` passes ``sketch=True`` to ``serve_model`` for a sketch's
    edit: ``model_config`` before the stage sends anything, and no revision. (Admitted: the plan
    covers soundless; the attempt's model does not.)"""
    class_id, sketch = await skenv.sketched()
    sent = [len(p.sent) for p in skenv.renv.providers]
    made = await skenv.renv.ask(class_id, sketch, "Seven bursts")
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    skenv.renv.soundless_covered = False
    try:
        skenv.renv.use("soundless_revise_good")  # the runtime reads the flag when it is built
        await skenv.renv.run("revise_draft")
    finally:
        skenv.renv.soundless_covered = True
    row = skenv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert [len(p.sent) for p in skenv.renv.providers][: len(sent)] == sent
    assert skenv.renv.current(class_id) == sketch
    assert len(skenv.renv.revisions(class_id)) == 1  # the sketch only
