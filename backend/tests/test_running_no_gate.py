"""Running with no gate (ADR 0018 Amendment 6, #1544; owner, 2026-11-26: running is always
available), against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider`` scripts.
Never the network, never a real account or model.

It replaces ``test_running_gate`` (the switch, the stored flag, the coverage order of ``admit`` and
the worker's ``feature_off`` and ``model_config`` backstops), which pinned what this issue retires
on purpose. Covered: with nothing stored and no gate configured, ``GET /api/connections`` says
nothing about running and a running design is admitted with one credit; with no route at all (the
bootstrap choice, which never covers running) the worker designs it on the uncovered model with
the stale-route warning instead of ``model_config``; a revise of a running class is admitted with no
route. The class's fixed sport and the served-with-a-warning revise are ``test_running_jobs``'.
"""

import json
import logging
from typing import Any
from uuid import UUID

import pytest

from classbeat import design
from classbeat.agent.schemas import RunningDraft
from classbeat.design import sport
from designtools import FakeMowlMusic
from jobapi import new_key
from modeltools import clear_gate
from optools import PLAYLIST
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

RUN: dict[str, Any] = {"sport": "running", "length_min": 30, "intensity": "moderate"}


@pytest.fixture(autouse=True)
def no_route(renv: ReviseEnv) -> None:
    """No route anywhere and nothing stored: only the bootstrap choice, which covers no running."""
    clear_gate(renv.denv.ops.admin)
    assert renv.sql("SELECT count(*) FROM app_settings").scalar_one() == 0
    sport._warned.clear()  # pyright: ignore[reportPrivateUsage]


def design_body(renv: ReviseEnv) -> dict[str, Any]:
    values = {
        "title": "Tuesday run",
        "brief": RUN,
        "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
        "mowl_connection_id": str(renv.browser.connection_id),
    }
    return {"kind": design.KIND, "input": values}


async def create(renv: ReviseEnv, body: dict[str, Any]) -> Any:
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def credits(renv: ReviseEnv) -> int:
    o = renv.owner
    return renv.sql("SELECT count(*) FROM credit_ledger WHERE owner_id = :o", o=o).scalar_one()


async def test_connections_say_nothing_about_running(renv: ReviseEnv) -> None:
    got = await renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    assert "running_enabled" not in got.json()


async def test_a_running_design_on_an_uncovered_model_is_designed_with_the_warning(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, caplog: pytest.LogCaptureFixture
) -> None:
    renv.running_covered = False  # what the worker resolves: the bootstrap choice
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    assert credits(renv) == 1  # admitted like any design: one class credit
    with caplog.at_level(logging.WARNING, logger=sport.__name__):
        await renv.run(design.KIND)
    job_id = UUID(made.json()["id"])
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)  # never model_config
    RunningDraft.model_validate_json(json.dumps(renv.denv.revisions(job_id)[0].content))
    warned = [r for r in caplog.records if r.getMessage() == "model route stale"]
    assert [r.__dict__["reason"] for r in warned] == [sport.UNCOVERED]


async def test_a_revise_of_a_running_class_is_admitted_with_no_route(renv: ReviseEnv) -> None:
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv))
    assert made.status_code == 201, made.text
    await renv.run(design.KIND)
    job_id = UUID(made.json()["id"])
    class_id, base = UUID(made.json()["class_id"]), renv.denv.revisions(job_id)[0].id
    renv.running_covered = False
    renv.use("running_revise")
    asked = await renv.ask(class_id, base)
    assert asked.status_code == 201, asked.text  # no running_not_evaluated, no feature_off
