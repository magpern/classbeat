"""The interpretation's guards carried over from the #903 review (#904), against Postgres as
``classbeat_app`` (and the superuser where RLS must be out of the way), the fake MOWL and
``FakeProvider``: the worker's store refuses a class that already has an interpretation; the read
filters on ``owner_id`` itself; the hand-off refuses a class designed some other way and answers
an existing design job on ``idempotency_mismatch``; a stored ``not_connected`` starts again as a
new interpret job (``interpret-again``). Never the network, a real account or a model."""

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agentfake import provider
from classbeat import design
from classbeat.design import INTERPRET_REQUEST, interpretation_row
from classbeat.design.interpretation_row import handoff_key
from designtools import BRIEF, PLAYLIST
from interprettools import REQUEST, InterpretEnv, interpreter
from jobapi import new_key

pytestmark = pytest.mark.usefixtures("jenv")


async def design_job(ienv: InterpretEnv, class_id: UUID, key: str, **brief: Any) -> Any:
    body = {
        "kind": design.KIND,
        "input": {
            "class_id": str(class_id),
            "brief": BRIEF | brief,
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
            "mowl_connection_id": str(ienv.renv.browser.connection_id),
        },
    }
    return await ienv.renv.browser.post("/api/jobs", body, {"Idempotency-Key": key})


async def test_the_worker_never_overwrites_an_interpretation_already_stored(
    ienv: InterpretEnv,
) -> None:
    """``interpretation_row._STORE``: only a class with no interpretation and no revision."""
    ienv.models(interpreter())
    made = await ienv.start()
    assert made.status_code == 201, made.text
    job_id, class_id = UUID(made.json()["id"]), UUID(made.json()["class_id"])
    planted = {"planted": True}
    ienv.sql(
        "UPDATE classes SET interpretation = CAST(:v AS jsonb) WHERE id = :c",
        v=json.dumps(planted),
        c=class_id,
    )
    await ienv.run(INTERPRET_REQUEST.name)
    row = ienv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "internal")
    assert ienv.interpretation(class_id) == planted  # nothing written over it


async def test_the_read_filters_on_the_owner_even_without_rls(
    ienv: InterpretEnv, database_url: str
) -> None:
    """As the superuser (RLS does not apply), the read still answers only the owner's row."""
    class_id, _ = await ienv.interpreted()
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as db:
            assert await interpretation_row.read(db, ienv.owner, class_id) is not None
            assert await interpretation_row.read(db, UUID(int=1), class_id) is None
            assert await interpretation_row.read(db, UUID(int=1), class_id, lock=True) is None
    finally:
        await engine.dispose()


async def test_a_class_designed_some_other_way_is_refused_without_a_credit(
    ienv: InterpretEnv,
) -> None:
    class_id, _ = await ienv.interpreted()
    ienv.models(provider("good_first_draft"))
    made = await design_job(ienv, class_id, new_key())
    assert made.status_code == 201, made.text
    await ienv.run(design.KIND)
    assert ienv.renv.current(class_id) is not None
    before = ienv.counts()
    refused = await ienv.handoff(class_id)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "class_designed")
    assert ienv.counts() == before  # no job, no credit
    assert ienv.interpretation(class_id)["refusal"] is None  # derived, never stored


async def test_a_completed_handoff_is_still_answered_with_its_job(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted()
    first = await ienv.handoff(class_id)
    assert first.status_code == 201, first.text
    ienv.models(provider("good_first_draft"))
    await ienv.run(design.KIND)
    assert ienv.renv.current(class_id) is not None
    again = await ienv.handoff(class_id)
    assert again.status_code == 200, again.text
    assert again.json()["job_id"] == first.json()["job_id"]


async def test_a_mismatch_with_an_existing_design_job_answers_that_job(ienv: InterpretEnv) -> None:
    class_id, interpret_job = await ienv.interpreted()
    planted = await design_job(ienv, class_id, handoff_key(interpret_job), length_min=35)
    assert planted.status_code == 201, planted.text
    before = ienv.counts()
    answer = await ienv.handoff(class_id)
    assert answer.status_code == 200, answer.text
    assert answer.json()["job_id"] == planted.json()["id"]
    assert ienv.interpretation(class_id)["refusal"] is None  # not stored as a refusal
    assert ienv.counts() == before


def refuse_not_connected(ienv: InterpretEnv, class_id: UUID) -> None:
    stored = ienv.interpretation(class_id)
    stored["refusal"] = {"reason": "not_connected", "counts": {}}
    ienv.sql(
        "UPDATE classes SET interpretation = CAST(:v AS jsonb) WHERE id = :c",
        v=json.dumps(stored),
        c=class_id,
    )


async def test_a_stored_not_connected_starts_again_as_a_new_interpret_job(
    ienv: InterpretEnv,
) -> None:
    class_id, first_job = await ienv.interpreted()
    refuse_not_connected(ienv, class_id)
    classes, jobs, credits = ienv.counts()
    path = f"/api/classes/{class_id}/interpret-again"
    made = await ienv.renv.browser.post(path, None)
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["state"]) == ("interpret_request", "queued")
    assert ienv.counts() == (classes + 1, jobs + 1, credits)  # a new class, no credit
    new_job = UUID(body["job_id"])
    given = ienv.stored_input(new_job)
    assert given["request"] == REQUEST
    assert given["mowl_connection_id"] == str(ienv.renv.browser.connection_id)
    new_class = ienv.sql("SELECT class_id FROM jobs WHERE id = :j", j=new_job).scalar_one()
    assert new_class != class_id
    again = await ienv.renv.browser.post(path, None)
    assert (again.status_code, again.json()) == (200, body)  # the same job, from any tab
    assert ienv.interpretation(class_id)["job_id"] == str(first_job)  # the old class is kept

    ienv.models(interpreter())
    await ienv.run(INTERPRET_REQUEST.name)
    assert ienv.renv.denv.ops.row(new_job).state == "completed"
    assert ienv.interpretation(new_class)["refusal"] is None


async def test_interpret_again_needs_a_stored_not_connected(
    ienv: InterpretEnv, browsers: Any
) -> None:
    class_id, _ = await ienv.interpreted()
    path = f"/api/classes/{class_id}/interpret-again"
    refused = await ienv.renv.browser.post(path, None)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_ready")
    refuse_not_connected(ienv, class_id)
    other = await browsers()
    for who, cid in ((other, class_id), (ienv.renv.browser, UUID(int=7))):
        answer = await who.post(f"/api/classes/{cid}/interpret-again", None)
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (404, "not_found")
