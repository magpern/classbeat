"""A revise of a class with stated values (ADR 0029 section 4, #904) through the jobs API, the
worker side, Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider`` scripts: the
request's stated values are read by the ``interpret`` stage, layered onto the class's, checked by
the revise stage's gate and recorded in ``provenance.stated``, so the next revise keeps them.
Never the network, a real account or a model."""

import copy
import json
from typing import Any
from uuid import UUID

import pytest

from agentfake import CONFIG, FactoryModels, from_dict, script
from classbeat import design
from classbeat.agent.interpret import FINAL_TOOL as SUBMIT_BRIEF
from classbeat.agent.providers.fake import FakeProvider
from classbeat.design import Runtime
from jobapi import new_key
from optools import PLAYLIST
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

STATED_BRIEF: dict[str, Any] = {
    "length_min": 30,
    "intensity": "moderate",
    "climbs": 1,
    "sprints": 2,
    "explicit": ["length_min", "climbs"],
}


def thirty() -> dict[str, Any]:
    return copy.deepcopy(script("revise_good").drafts["revised"])


def twenty_five() -> dict[str, Any]:
    """The 30 minute revised draft without its 390 s tempo block and with 90 s more cooldown."""
    draft = thirty()
    draft["segments"] = [s for s in draft["segments"] if s["name"] != "Tempo"]
    draft["segments"][-1]["intervals"][0]["sec"] += 90
    return draft


def easier(draft: dict[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(draft)
    changed["segments"][0]["intervals"][1]["zone"] = 2
    return changed


def interpreted(reading: dict[str, Any]) -> dict[str, Any]:
    return {"calls": [{"name": SUBMIT_BRIEF, "input": reading, "expect": {"accepted": True}}]}


def submit(draft: dict[str, Any], expect: dict[str, Any] | None = None) -> dict[str, Any]:
    call: dict[str, Any] = {"name": "submit_draft", "input": {"draft": draft, "note": "Done."}}
    if expect is not None:
        call["expect"] = expect
    return {"calls": [call]}


def use(renv: ReviseEnv, *turns: dict[str, Any]) -> list[FakeProvider]:
    made: list[FakeProvider] = []

    def build() -> FakeProvider:
        provider = from_dict({"model": "fake-model", "turns": list(turns)})
        made.append(provider)
        return provider

    design.configure(
        Runtime(keyring=renv.denv.ops.keyring, models=FactoryModels(build), agent=CONFIG)
    )
    return made


async def stated_class(renv: ReviseEnv) -> tuple[UUID, UUID]:
    """A class designed from a brief that states its length and its climbs."""
    renv.use("good_first_draft")
    body = {
        "kind": design.KIND,
        "input": {
            "title": "Tuesday climb",
            "brief": STATED_BRIEF,
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    await renv.run(design.KIND)
    assert renv.denv.ops.row(job_id).state == "completed"
    return UUID(made.json()["class_id"]), renv.denv.revisions(job_id)[0].id


async def revise(renv: ReviseEnv, class_id: UUID, base: UUID, request: str) -> UUID:
    made = await renv.ask(class_id, base, request)
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    await renv.run(design.REVISE_KIND)
    return job_id


def provenance(row: Any) -> dict[str, Any]:
    raw = row.provenance
    return raw if isinstance(raw, dict) else json.loads(raw)


def items(row: Any) -> dict[str, tuple[str, Any]]:
    return {i["path"]: (i["origin"], i["value"]) for i in provenance(row)["interpretation"]}


def refusals(renv: ReviseEnv, job_id: UUID) -> list[dict[str, Any]]:
    return [
        p for _, kind, p, _ in renv.denv.ops.events(job_id) if kind == "tool_called" and not p["ok"]
    ]


async def test_revising_a_stated_length_succeeds_and_the_other_stated_values_survive(
    renv: ReviseEnv,
) -> None:
    class_id, base = await stated_class(renv)
    providers = use(
        renv,
        interpreted({"length_min": {"value": 25, "origin": "explicit", "quote": "25 minutes"}}),
        submit(twenty_five(), {"accepted": True}),
    )
    job_id = await revise(renv, class_id, base, "Make it 25 minutes.")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category, row.stage) == ("completed", None, "revise")
    assert refusals(renv, job_id) == []  # no correction round: the layered length was kept

    # The interpreter was told the class's values, never the first request's text.
    [provider] = providers
    first, then = provider.sent[0].spec, provider.sent[1].spec
    assert first.stage == "interpret"
    told = json.loads(first.first_message)
    assert told["current"]["length_min"] == 30
    assert told["request"] == "Make it 25 minutes."
    assert "brief" not in told  # never the first request's text
    # The revise stage got the layered stated block.
    assert then.stage == "revise"
    assert '"explicit":{"length_min":25,"climbs":1}' in then.first_message

    stages = [r.stage for r in renv.denv.agent_runs(job_id)]
    assert stages == ["interpret", "revise"]  # both runs count toward the job's cost

    _, second = renv.revisions(class_id)
    made = provenance(second)
    assert made["stated"]["length_min"] == 25
    assert made["stated"]["explicit"] == ["length_min", "climbs"]
    assert items(second)["length_min"] == ("explicit", 25)
    assert items(second)["climbs"] == ("explicit", 1)  # untouched, still as asked
    # The class's own brief stays as the first design stored it.
    brief = renv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert brief["length_min"] == 30


async def test_the_next_revise_starts_from_the_layered_values(renv: ReviseEnv) -> None:
    class_id, base = await stated_class(renv)
    use(
        renv,
        interpreted({"length_min": {"value": 25, "origin": "explicit"}}),
        submit(twenty_five(), {"accepted": True}),
    )
    await revise(renv, class_id, base, "Make it 25 minutes.")
    _, second = renv.revisions(class_id)

    # A request that states nothing: the 25 minutes stated before are kept, not the brief's 30.
    providers = use(renv, interpreted({}), submit(easier(twenty_five()), {"accepted": True}))
    job_id = await revise(renv, class_id, second.id, "Make the start easier.")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert refusals(renv, job_id) == []
    told = json.loads(providers[0].sent[0].spec.first_message)
    assert told["current"]["length_min"] == 25
    third = renv.revisions(class_id)[2]
    assert provenance(third)["stated"]["length_min"] == 25
    assert items(third)["length_min"] == ("explicit", 25)


async def test_a_miss_after_the_correction_budget_is_recorded_not_as_asked(
    renv: ReviseEnv,
) -> None:
    class_id, base = await stated_class(renv)
    refused = {"ok": False, "error": "invalid_draft"}
    use(
        renv,
        interpreted({"length_min": {"value": 25, "origin": "explicit"}}),
        submit(easier(thirty()), refused),
        submit(easier(thirty()), refused),
        submit(easier(thirty()), refused),
        submit(easier(thirty()), {"accepted": True}),
    )
    job_id = await revise(renv, class_id, base, "Make it 25 minutes.")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert len(refusals(renv, job_id)) == 3
    _, second = renv.revisions(class_id)
    found = {i["path"]: i for i in provenance(second)["interpretation"]}
    assert (found["length_min"]["origin"], found["length_min"]["reason_code"]) == (
        "not_as_asked",
        "length_off",
    )


async def test_a_class_that_states_nothing_is_revised_without_an_interpret_run(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    job_id, _ = await renv.revise(class_id, base, "revise_good", "Make the start easier.")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert [r.stage for r in renv.denv.agent_runs(job_id)] == ["revise"]
    _, second = renv.revisions(class_id)
    assert "stated" not in provenance(second)
