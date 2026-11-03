"""Running classes through the jobs API and the worker side (ADR 0018 sections 1, 2 and 5; M8 R1a,
#463), against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider`` scripts. Never
the network, never a real account or model.

Covered: the ``running_enabled`` matrix at creation (environment off, admin flag off, both on) and
in the worker (a queued running design or revise whose flag went off ends ``feature_off`` and calls
nothing); a running class designed and revised end to end on the running prompts and schema, its
brief stored with the sport, its revision, provenance, views and export; the refusals at creation
(a bike-only knob, a Swedebeat mix, song picking, a class of the other sport) and in the final
store (two queued jobs of different sports); Save refusing a running class until R1b; a running
design or revise on a model whose recorded result does not cover running ends ``model_config``
before any request (ADR 0012 amendment of 2026-11-03, issue #465), while cycling is unaffected.
"""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat import design
from classbeat.account.export import build_export
from classbeat.agent.records import Provenance
from classbeat.agent.running import DESIGN_SYSTEM_PROMPT, REVISE_SYSTEM_PROMPT
from classbeat.agent.schemas import RunningDraft
from classbeat.agent.stage import prompt_version
from classbeat.design import sport
from classbeat.models.versions import all_prompt_versions
from classbeat.songs.inputs import DesignWithSongsInput
from ctx import make_context
from designtools import BRIEF, FakeMowlMusic
from jobapi import new_key
from jobtools import new_job
from optools import PLAYLIST
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

RUN: dict[str, Any] = {"sport": "running", "length_min": 30, "intensity": "moderate"}


@pytest.fixture(autouse=True)
def running_on(renv: ReviseEnv) -> Iterator[None]:
    """The environment's switch on, in the web and the worker alike (one process here); after
    ``renv``, whose app set it from its settings (off) when it was built."""
    sport.configure(sport.SportGate(running_enabled=True))
    try:
        yield
    finally:
        sport.configure(None)


def admin_flag(renv: ReviseEnv, value: str | None) -> None:
    renv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    if value is not None:
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb))",
            v=json.dumps(value),
        )


def design_body(renv: ReviseEnv, brief: dict[str, Any], **more: Any) -> dict[str, Any]:
    values = {
        "title": "Tuesday run",
        "brief": brief,
        "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
        "mowl_connection_id": str(renv.browser.connection_id),
    }
    return {"kind": design.KIND, "input": values | more}


async def create(renv: ReviseEnv, body: dict[str, Any]) -> Any:
    return await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


def jobs(renv: ReviseEnv) -> int:
    return renv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=renv.owner).scalar_one()


async def running_class(renv: ReviseEnv) -> tuple[UUID, UUID, UUID]:
    """A running class designed by the worker: (class id, job id, revision id)."""
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv, RUN))
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    return UUID(made.json()["class_id"]), job_id, renv.denv.revisions(job_id)[0].id


# --- the flag ------------------------------------------------------------------------------------


@pytest.mark.parametrize("off", ["environment", "database"])
async def test_a_running_design_is_refused_feature_off_while_the_flag_is_off(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, off: str
) -> None:
    if off == "environment":
        sport.configure(sport.SportGate(running_enabled=False))
        admin_flag(renv, "on")  # the database can never switch it on past the environment
    else:
        admin_flag(renv, "off")
    try:
        refused = await create(renv, design_body(renv, RUN))
    finally:
        admin_flag(renv, None)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    assert jobs(renv) == 0
    assert fake_mowl.reads == []
    cycling = await create(renv, design_body(renv, BRIEF))  # cycling never reads the flag
    assert cycling.status_code == 201, cycling.text


@pytest.mark.parametrize("stored", [None, "on"])
async def test_a_running_design_is_admitted_with_both_switches_on(
    renv: ReviseEnv, stored: str | None
) -> None:
    admin_flag(renv, stored)
    try:
        made = await create(renv, design_body(renv, RUN))
    finally:
        admin_flag(renv, None)
    assert made.status_code == 201, made.text
    stored_input = renv.sql(
        "SELECT input FROM jobs WHERE id = :j", j=UUID(made.json()["id"])
    ).scalar_one()
    assert stored_input["brief"] == {**RUN, "style": "", "text": ""}  # with the sport


@pytest.mark.parametrize("off", ["environment", "database"])
async def test_a_queued_running_design_whose_flag_went_off_ends_feature_off_calling_nothing(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, off: str
) -> None:
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv, RUN))
    assert made.status_code == 201, made.text
    if off == "environment":
        sport.configure(sport.SportGate(running_enabled=False))
    else:
        admin_flag(renv, "off")
    try:
        await renv.run(design.KIND)
    finally:
        admin_flag(renv, None)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert renv.providers == []  # no model was built
    assert fake_mowl.reads == []
    assert fake_mowl.attaches == []


# --- designed and revised ------------------------------------------------------------------------


async def test_a_running_class_is_designed_on_the_running_prompt_and_stored_with_its_sport(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, job_id, revision_id = await running_class(renv)
    [fake] = renv.providers
    spec = fake.sent[0].spec
    assert all(s.spec.system == DESIGN_SYSTEM_PROMPT for s in fake.sent)
    assert (spec.stage, spec.system) == ("design", DESIGN_SYSTEM_PROMPT)
    preview = next(t for t in spec.tools if t.name == "preview_draft")
    assert "gradient" in json.dumps(preview.input_schema)
    assert "rpm" not in json.dumps([t.input_schema for t in spec.tools])
    brief = json.loads(spec.first_message[spec.first_message.index('{"brief"') :])["brief"]
    assert brief == {**RUN, "style": "", "text": ""}

    stored_brief = renv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert stored_brief == {**RUN, "style": "", "text": ""}
    [revision] = renv.revisions(class_id)
    draft = RunningDraft.model_validate_json(json.dumps(revision.content))
    assert sum(i.sec for s in draft.segments for i in s.intervals) == 1800
    assert "rpm" not in json.dumps(revision.content)
    made = Provenance.model_validate_json(json.dumps(revision.provenance))
    assert (made.sport, made.tss_approximate) == ("running", True)
    [run] = renv.denv.agent_runs(job_id)
    running_version = prompt_version(spec.system, spec.tools)
    assert made.prompt_version == run.prompt_version == running_version
    versions = all_prompt_versions()
    assert running_version == versions["running_design"]  # what a result records (#465)
    assert running_version not in (versions["design"], versions["revise"])  # not cycling's

    shown = await renv.browser.get(f"/api/jobs/{job_id}/revision")
    assert shown.status_code == 200, shown.text
    assert shown.json()["content"] == revision.content
    page = await renv.browser.get(f"/api/classes/{class_id}/revisions/{revision_id}")
    assert page.status_code == 200, page.text
    assert (page.json()["content"], page.json()["tss_approximate"]) == (revision.content, True)
    detail = await renv.browser.get(f"/api/classes/{class_id}")
    assert detail.json()["brief"]["sport"] == "running"

    document = await build_export(renv.denv.ops.factory, make_context(renv.owner))
    assert document is not None
    [exported] = [c for c in document["classes"] if c["id"] == str(class_id)]
    assert exported["brief"]["sport"] == "running"


async def test_a_running_class_is_revised_on_the_running_revise_prompt(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, _, base = await running_class(renv)
    job_id, _ = await renv.revise(class_id, base, "running_revise", "Easier warm-up")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    spec = renv.providers[-1].sent[0].spec
    assert (spec.stage, spec.system) == ("revise", REVISE_SYSTEM_PROMPT)
    _, second = renv.revisions(class_id)
    RunningDraft.model_validate_json(json.dumps(second.content))
    assert renv.current(class_id) == second.id
    _, assistant = renv.messages(class_id)
    assert assistant.change["intervals"]["changed"] == 2  # a zone and a gradient
    assert Provenance.model_validate_json(json.dumps(second.provenance)).sport == "running"


@pytest.mark.parametrize("off", ["at_creation", "in_the_worker"])
async def test_a_running_revise_is_refused_or_ends_feature_off_while_the_flag_is_off(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic, off: str
) -> None:
    class_id, _, base = await running_class(renv)
    renv.use("running_revise")
    if off == "at_creation":
        admin_flag(renv, "off")
        try:
            refused = await renv.ask(class_id, base)
        finally:
            admin_flag(renv, None)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["reason"] == "feature_off"
        return
    asked = await renv.ask(class_id, base)
    assert asked.status_code == 201, asked.text
    reads = len(fake_mowl.reads)
    built = len(renv.providers)
    sport.configure(sport.SportGate(running_enabled=False))
    await renv.run(design.REVISE_KIND)
    row = renv.denv.ops.row(UUID(asked.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "feature_off")
    assert (len(fake_mowl.reads), len(renv.providers)) == (reads, built)


# --- an evaluated model only (ADR 0012 amendment of 2026-11-03, issue #465) ---------------------


async def test_a_running_design_on_a_model_not_evaluated_for_running_ends_model_config(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    renv.running_covered = False
    renv.use("running_first_draft")
    made = await create(renv, design_body(renv, RUN))
    assert made.status_code == 201, made.text
    await renv.run(design.KIND)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert all(p.sent == [] for p in renv.providers)  # no model request
    assert (fake_mowl.reads, fake_mowl.attaches) == ([], [])  # no MOWL call
    assert renv.denv.revisions(UUID(made.json()["id"])) == []


async def test_a_running_revise_on_a_model_not_evaluated_for_running_ends_model_config(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, _, base = await running_class(renv)
    renv.running_covered = False
    renv.use("running_revise")
    asked = await renv.ask(class_id, base)
    assert asked.status_code == 201, asked.text
    reads = len(fake_mowl.reads)
    sent = sum(len(p.sent) for p in renv.providers)
    await renv.run(design.REVISE_KIND)
    row = renv.denv.ops.row(UUID(asked.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert (len(fake_mowl.reads), sum(len(p.sent) for p in renv.providers)) == (reads, sent)
    assert renv.current(class_id) == base


async def test_a_cycling_design_does_not_need_a_running_evaluation(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    renv.running_covered = False
    renv.use("good_first_draft")
    made = await create(renv, design_body(renv, BRIEF))
    assert made.status_code == 201, made.text
    await renv.run(design.KIND)
    row = renv.denv.ops.row(UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)


# --- refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "knob",
    [{"climbs": 1}, {"sprints": 0}, {"races": 2}, {"ftp_test": 20}, {"class_type": "ftp_test"}],
)
async def test_a_running_class_cannot_be_created_with_a_bike_only_field(
    renv: ReviseEnv, knob: dict[str, Any]
) -> None:
    refused = await create(renv, design_body(renv, {**RUN, **knob}))
    assert refused.status_code == 422, refused.text
    assert jobs(renv) == 0


async def test_a_running_class_designs_on_a_mowl_playlist_only(renv: ReviseEnv) -> None:
    mix = {"source": "mowl_swedebeat_mix", "mix_id": "mix-1"}
    refused = await create(renv, design_body(renv, RUN, music=mix))
    assert refused.status_code == 422, refused.text
    assert jobs(renv) == 0
    with pytest.raises(ValueError, match="cycling classes only"):
        DesignWithSongsInput.model_validate(
            {
                "title": "Tuesday run",
                "brief": RUN,
                "mowl_connection_id": str(renv.browser.connection_id),
                "spotify_connection_id": str(renv.browser.connection_id),
            }
        )


async def test_the_sport_of_a_designed_class_never_changes(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    running, _, _ = await running_class(renv)
    renv.use("good_first_draft")
    other = await create(renv, design_body(renv, BRIEF, class_id=str(running), title=None))
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["reason"] == "invalid_input"
    cycling, _ = await renv.design()
    back = await create(renv, design_body(renv, RUN, class_id=str(cycling), title=None))
    assert back.status_code == 409, back.text
    assert back.json()["detail"]["reason"] == "invalid_input"
    same = await create(renv, design_body(renv, RUN, class_id=str(running), title=None))
    assert same.status_code == 201, same.text


async def test_a_design_admitted_before_the_class_was_fixed_cannot_store_the_other_sport(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """A class has one open job at a time, so this is defence in depth: a running job queued for
    a class that a cycling design fixed since (inserted here past ``admit``) fails its final
    store ``invalid_input`` and stores nothing."""
    class_id, _ = await renv.design()
    values = design_body(renv, RUN, class_id=str(class_id))["input"]
    del values["title"]
    job_id = new_job(
        renv.denv.ops.admin, renv.owner, class_id, kind=design.KIND, input=json.dumps(values)
    )
    renv.use("running_first_draft")
    await renv.run(design.KIND)
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(renv.revisions(class_id)) == 1
    stored = renv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert stored["sport"] == "cycling"


async def test_save_refuses_a_running_class_until_r1b(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, _, revision_id = await running_class(renv)
    [revision] = renv.revisions(class_id)
    body = {
        "kind": "save_workout",
        "input": {
            "class_id": str(class_id),
            "revision_id": str(revision_id),
            "content_hash": bytes(revision.content_hash).hex(),
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    refused = await create(renv, body)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    saves = renv.sql("SELECT count(*) FROM jobs WHERE kind = 'save_workout'").scalar_one()
    assert saves == 0


async def test_save_refuses_by_the_classs_sport_not_by_the_shape_of_the_content(
    renv: ReviseEnv, fake_mowl: FakeMowlMusic
) -> None:
    """The refusal reads the class's stored brief: even content that is a valid cycling draft is
    never saved for a class whose sport is running (here a cycling class's brief rewritten)."""
    class_id, revision_id = await renv.design()
    [revision] = renv.revisions(class_id)
    renv.sql(
        "UPDATE classes SET brief = brief || CAST(:s AS jsonb) WHERE id = :c",
        s=json.dumps({"sport": "running"}),
        c=class_id,
    )
    body = {
        "kind": "save_workout",
        "input": {
            "class_id": str(class_id),
            "revision_id": str(revision_id),
            "content_hash": bytes(revision.content_hash).hex(),
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    refused = await create(renv, body)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
