"""The composer's sketch no longer depends on the soundless switch (ADR 0038 amendment of
2026-11-24, #1484), against Postgres, the fake MOWL and ``FakeProvider``: the sketch's design,
its chat edit and ``can_change`` need only the job's own plan's coverage; the composer is offered
per plan (``composer_available``); the hand-off records its route; a ride in progress follows its
class. A real no-music class keeps its gate (its tests are unchanged). Never the network, a real
account or a model."""

from typing import Any
from uuid import UUID

import pytest

from classbeat.design import music_kind
from sketchtools import SketchEnv
from test_soundless_jobs import own_key, plan_keys, route

pytestmark = pytest.mark.usefixtures("jenv")


def gate(*, enabled: bool, fake: bool) -> None:
    music_kind.configure(music_kind.SoundlessGate(enabled=enabled, fake_covers=fake))


async def answers(skenv: SketchEnv, class_id: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    browser = skenv.renv.browser
    detail = (await browser.get(f"/api/classes/{class_id}")).json()
    history = (await browser.get(f"/api/classes/{class_id}/history")).json()
    return detail, history


async def connections(skenv: SketchEnv) -> dict[str, Any]:
    got = await skenv.renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    return got.json()


# --- 1. the switch off, the plan covered ------------------------------------------------------


async def test_a_sketch_is_designed_and_edited_with_the_soundless_switch_off(
    skenv: SketchEnv,
) -> None:
    gate(enabled=False, fake=True)
    flags = await connections(skenv)
    assert (flags["composer_flow"], flags["soundless_enabled"]) == (True, False)
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
    gate(enabled=True, fake=False)  # nothing covers soundless
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
    gate(enabled=False, fake=True)
    class_id = await skenv.interpreted()
    gate(enabled=False, fake=False)  # the plan's route is cleared before the hand-off
    made = await skenv.ienv.handoff(class_id)
    assert made.status_code == 201, made.text  # no ``feature_off``, no ``soundless_not_evaluated``
    [job] = skenv.ienv.design_jobs(class_id)
    assert (job.kind, skenv.ienv.stored_input(job.id).get("mode")) == ("design_draft", None)
    assert skenv.ienv.interpretation(class_id)["route"] == "classic"
    detail, _ = await answers(skenv, class_id)
    assert detail["composer"] is False  # a class that fell back is an ordinary class
    gate(enabled=False, fake=True)
    again = await skenv.ienv.handoff(class_id)  # a reopened tab, with coverage back: same job
    assert (again.status_code, again.json()["job_id"]) == (200, str(job.id))
    assert skenv.ienv.interpretation(class_id)["route"] == "classic"  # the route never moves


# --- 3. two plans, one covered ----------------------------------------------------------------


async def test_the_composer_is_offered_per_plan_not_per_every_plan(skenv: SketchEnv) -> None:
    gate(enabled=True, fake=False)
    mine = await own_key(skenv.renv)
    other = next(k for k in plan_keys(skenv.renv) if k != mine)
    route(skenv.renv, mine)  # only the user's own plan is covered
    flags = await connections(skenv)
    assert (flags["composer_flow"], flags["soundless_enabled"]) == (True, False)
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
    gate(enabled=False, fake=False)  # coverage removed
    detail, history = await answers(skenv, class_id)
    assert detail["composer"] is True  # the class's route, not the current flag
    assert (detail["can_change"], history["can_change"]) == (False, False)
    refused = await skenv.renv.ask(class_id, sketch, "Easier start")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "soundless_not_evaluated"
    assert len(skenv.renv.messages(class_id)) == 1  # nothing written
    assert (await connections(skenv))["composer_flow"] is False  # a new class: the older route
