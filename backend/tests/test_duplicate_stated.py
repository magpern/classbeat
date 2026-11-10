"""A duplicate copies the stated layer of the revision it copies (ADR 0029 section 4, #907)
through the real duplicate route and the real revise path, Postgres as ``classbeat_app``, the
fake MOWL and ``FakeProvider`` scripts. Never the network, a real account or a model."""

import json
from typing import Any
from uuid import UUID

import pytest

from revisetools import ReviseEnv
from test_revise_stated import (
    easier,
    interpreted,
    provenance,
    refusals,
    revise,
    stated_class,
    submit,
    twenty_five,
    use,
)

pytestmark = pytest.mark.usefixtures("jenv")


async def duplicate(renv: ReviseEnv, class_id: UUID, revision: Any) -> UUID:
    body = {
        "revision_id": str(revision.id),
        "content_hash": bytes(revision.content_hash).hex(),
        "title": "Tuesday climb (copy)",
    }
    got = await renv.browser.post(
        f"/api/classes/{class_id}/duplicate", body, {"Idempotency-Key": "k" * 20}
    )
    assert got.status_code == 201, got.text
    return UUID(got.json()["class_id"])


async def revised_to_25(renv: ReviseEnv) -> tuple[UUID, Any]:
    class_id, base = await stated_class(renv)
    use(
        renv,
        interpreted({"length_min": {"value": 25, "origin": "explicit"}}),
        submit(twenty_five(), {"accepted": True}),
    )
    await revise(renv, class_id, base, "Make it 25 minutes.")
    return class_id, renv.revisions(class_id)[1]


async def test_a_duplicate_of_a_revised_stated_length_revises_from_the_revised_value(
    renv: ReviseEnv,
) -> None:
    class_id, second = await revised_to_25(renv)
    copy_id = await duplicate(renv, class_id, second)

    [first] = renv.revisions(copy_id)
    # The layer is the copy's own, the brief stays as the first design stored it.
    assert provenance(first)["stated"] == provenance(second)["stated"]
    assert provenance(first)["stated"]["length_min"] == 25
    brief = renv.sql("SELECT brief FROM classes WHERE id = :c", c=copy_id).scalar_one()
    source = renv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert brief == source
    assert brief["length_min"] == 30

    # The copy's first revise: the interpreter is told 25, not 30, and the gate checks 25.
    providers = use(renv, interpreted({}), submit(easier(twenty_five()), {"accepted": True}))
    job_id = await revise(renv, copy_id, first.id, "Make the start easier.")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert refusals(renv, job_id) == []
    [provider] = providers
    sent = provider.sent[0].spec
    assert sent.stage == "interpret"
    assert json.loads(sent.first_message)["current"]["length_min"] == 25
    assert '"explicit":{"length_min":25,"climbs":1}' in provider.sent[1].spec.first_message
    second_copy = renv.revisions(copy_id)[1]
    assert provenance(second_copy)["stated"]["length_min"] == 25


async def test_a_duplicate_of_a_duplicate_keeps_the_layer(renv: ReviseEnv) -> None:
    class_id, second = await revised_to_25(renv)
    once = await duplicate(renv, class_id, second)
    [first] = renv.revisions(once)
    body = {
        "revision_id": str(first.id),
        "content_hash": bytes(first.content_hash).hex(),
        "title": "Tuesday climb (copy 2)",
    }
    got = await renv.browser.post(
        f"/api/classes/{once}/duplicate", body, {"Idempotency-Key": "j" * 20}
    )
    assert got.status_code == 201, got.text
    [twice] = renv.revisions(UUID(got.json()["class_id"]))
    assert provenance(twice)["stated"]["length_min"] == 25


async def test_a_duplicate_of_a_class_without_a_layer_is_as_before(renv: ReviseEnv) -> None:
    class_id, _ = await revised_to_25(renv)
    first = renv.revisions(class_id)[0]  # the first design: no layer anywhere up its chain
    assert "stated" not in provenance(first)
    copy_id = await duplicate(renv, class_id, first)
    [copied] = renv.revisions(copy_id)
    assert "stated" not in provenance(copied)
    assert provenance(copied)["origin"] == "duplicate"
    brief = renv.sql("SELECT brief FROM classes WHERE id = :c", c=copy_id).scalar_one()
    assert brief["length_min"] == 30
