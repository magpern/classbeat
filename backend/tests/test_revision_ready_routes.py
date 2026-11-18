"""Every route and job that takes a revision id refuses a sketch with ``not_ready`` (ADR 0038 section
4, #1186), against Postgres as ``classbeat_app`` and the fakes: Save, Update, Send to player,
Refit, Change songs and a studio revise through ``POST /api/jobs``; duplicate, the pick of the
current revision and the beat-one correction through their own routes. Nothing is created and the
fake MOWL is asked nothing: no job, no operation, no class, no correction. Never the network, a
real account or a model."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat import connections
from ctx import make_context
from designtools import FakeMowlMusic
from jobapi import new_key
from sketchtools import SketchEnv

pytestmark = pytest.mark.usefixtures("jenv")


def written(skenv: SketchEnv) -> tuple[int, ...]:
    """What a refused call must not add to: the user's jobs, operations, classes, revisions,
    corrections and player publications."""
    o = skenv.renv.owner
    return tuple(
        skenv.sql(f"SELECT count(*) FROM {table} WHERE owner_id = :o", o=o).scalar_one()  # noqa: S608
        for table in (
            "jobs",
            "external_operations",
            "classes",
            "class_revisions",
            "beat_one_corrections",
            "player_publications",
        )
    )


async def job(skenv: SketchEnv, kind: str, values: dict[str, Any]) -> httpx.Response:
    body = {"kind": kind, "input": values}
    return await skenv.renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})


async def connect_swedebeat(skenv: SketchEnv) -> UUID:
    """A SwedeBeat connection row (a fake bearer, never a real one; nothing reaches a gateway)."""
    ops = skenv.renv.denv.ops
    return await connections.save(
        ops.factory,
        ops.keyring,
        make_context(skenv.renv.owner),
        "swedebeat",
        {"bearer": "fake-bearer-for-tests"},
        public={"email": "rider@example.com"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def call(skenv: SketchEnv, case: str, class_id: UUID, sketch: UUID) -> httpx.Response:
    browser = skenv.renv.browser
    digest = bytes(skenv.revision(sketch).content_hash).hex()
    mowl = str(browser.connection_id)
    ids = {"class_id": str(class_id), "revision_id": str(sketch), "content_hash": digest}
    if case == "save_workout":
        return await job(skenv, case, ids | {"mowl_connection_id": mowl})
    if case == "update_workout":
        more = {"mowl_connection_id": mowl, "saved_workout_id": str(uuid.uuid4())}
        return await job(skenv, case, ids | more)
    if case == "send_to_player":
        return await job(skenv, case, ids)
    base = {"class_id": str(class_id), "base_revision_id": str(sketch)}
    if case == "refit_draft":
        return await job(skenv, case, base | {"mowl_connection_id": mowl})
    if case == "revise_songs":
        spotify = str(await skenv.ienv.connect_spotify())
        more = {"mowl_connection_id": mowl, "spotify_connection_id": spotify, "request": "Faster", "replace": "all"}
        return await job(skenv, case, base | more)
    if case == "studio_revise":
        more = {"swedebeat_connection_id": str(await connect_swedebeat(skenv)), "request": "Up"}
        return await job(skenv, case, base | more)
    if case == "duplicate":
        body = {"revision_id": str(sketch), "content_hash": digest, "title": "Copy"}
        extra = {"Idempotency-Key": new_key()}
        return await browser.post(f"/api/classes/{class_id}/duplicate", body, extra)
    if case == "current":
        body = {"revision_id": str(sketch), "content_hash": digest}
        return await browser.post(f"/api/classes/{class_id}/current", body)
    headers = await browser.headers()
    path = f"/api/classes/{class_id}/beat-one/1"
    if case == "beat_one_set":
        body = {"revision_id": str(sketch), "beats": 1}
        answer = await browser.client.put(path, headers=headers, json=body)
    else:
        answer = await browser.client.delete(path, headers=headers, params={"revision_id": sketch})
    browser.client.cookies.clear()
    return answer


CASES = [
    "save_workout",
    "update_workout",
    "send_to_player",
    "refit_draft",
    "revise_songs",
    "studio_revise",
    "duplicate",
    "current",
    "beat_one_set",
    "beat_one_clear",
]


@pytest.mark.parametrize("case", CASES)
async def test_every_route_refuses_a_sketch_before_anything_else(
    skenv: SketchEnv, fake_mowl: FakeMowlMusic, case: str
) -> None:
    class_id, sketch = await skenv.sketched()
    before, mowl = written(skenv), len(fake_mowl.requests)
    answer = await call(skenv, case, class_id, sketch)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "not_ready"
    assert written(skenv) == before  # no job, no operation, no copy, no correction
    assert len(fake_mowl.requests) == mowl  # nothing reached MOWL
    assert skenv.renv.current(class_id) == sketch  # the sketch stays current
