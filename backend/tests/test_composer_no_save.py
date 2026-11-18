"""While a fill waits, is approved, replaced, cancelled or expires, the class's current revision is
still the sketch and there is no draft revision to save (ADR 0038 sections 4, 5 and 12, #1188):
Save, Update, Send to player, duplicate and the pick of the current revision refuse, and the fake
MOWL is asked nothing (zero writes). An approved list whose phase 2 has not run yet is the
same (its job is queued); phase 2 itself, the first writes, is ``test_composer_phase2.py``
(#1189). Expiry moves nothing and Find songs again starts a new
fill; a swap cannot start from an expired list. Never the network, a real account or a model."""

import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.jobs import queue
from composertools import ComposerEnv, swap
from jobapi import new_key
from songtools import Rider

pytestmark = pytest.mark.usefixtures("jenv")

STATES = ["waiting", "approved", "replaced", "cancelled", "expired"]
CASES = ["save_workout", "update_workout", "send_to_player", "duplicate", "current"]


async def call(rider: Rider, case: str, class_id: UUID, revision: UUID, digest: str) -> Any:
    ids = {"class_id": str(class_id), "revision_id": str(revision), "content_hash": digest}
    mowl = str(rider.mowl_connection_id)
    browser = rider.browser
    if case in ("save_workout", "update_workout", "send_to_player"):
        values: dict[str, Any] = dict(ids)
        if case != "send_to_player":
            values["mowl_connection_id"] = mowl
        if case == "update_workout":
            values["saved_workout_id"] = str(uuid.uuid4())
        body = {"kind": case, "input": values}
        return await browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    if case == "duplicate":
        body = {"revision_id": str(revision), "content_hash": digest, "title": "Copy"}
        path = f"/api/classes/{class_id}/duplicate"
        return await browser.post(path, body, {"Idempotency-Key": new_key()})
    body = {"revision_id": str(revision), "content_hash": digest}
    return await browser.post(f"/api/classes/{class_id}/current", body)


async def reach(cenv: ComposerEnv, rider: Rider, class_id: UUID, state: str) -> None:
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    if state == "approved":
        answer = await rider.approve(job_id, list_id, digest)
        assert answer.status_code == 200, answer.text
        assert cenv.songenv.row(job_id).state == "queued"  # phase 2 has not run (#1189)
    elif state == "replaced":
        made = await swap(rider, class_id, list_id)
        assert made.status_code == 201, made.text
    elif state == "cancelled":
        answer = await rider.browser.post(f"/api/jobs/{job_id}/cancel", None)
        assert answer.status_code == 200, answer.text
    elif state == "expired":
        cenv.sql("UPDATE jobs SET expires_at = now() - interval '1 minute' WHERE id = :j", j=job_id)
        await queue.expire(cenv.songenv.factory)
        assert cenv.songenv.row(job_id).state == "expired"


@pytest.mark.parametrize("state", STATES)
async def test_nothing_can_be_saved_while_the_songs_are_found(
    cenv: ComposerEnv, state: str
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    await reach(cenv, rider, class_id, state)
    assert cenv.current(class_id) == sketch
    assert cenv.drafts(class_id) == 1  # the sketch alone: no draft revision to name
    digest = cenv.sql(
        "SELECT encode(content_hash, 'hex') FROM class_revisions WHERE id = :r", r=sketch
    ).scalar_one()
    mowl = len(cenv.songenv.mowl.requests)
    for case in CASES:
        answer: httpx.Response = await call(rider, case, class_id, sketch, digest)
        assert answer.status_code == 409, (case, answer.text)
        # A job of the class still open (waiting, or the swap queued) refuses a new job first.
        refusals = (
            {"not_ready", "class_busy"}
            if state in ("waiting", "approved", "replaced")
            else {"not_ready"}
        )
        assert answer.json()["detail"]["reason"] in refusals, case
    assert len(cenv.songenv.mowl.requests) == mowl  # zero MOWL calls, so zero writes


async def test_after_expiry_find_songs_again_is_a_new_fill_and_a_swap_is_refused(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    job_id, list_id, _ = await cenv.waiting(rider, class_id)
    cenv.sql("UPDATE jobs SET expires_at = now() - interval '1 minute' WHERE id = :j", j=job_id)
    refused = await swap(rider, class_id, list_id)  # past expires_at, before the sweep
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "not_waiting")
    await queue.expire(cenv.songenv.factory)
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["fill_songs"]["failure"] == "expired"
    again = await cenv.fill(rider, class_id, retry=True)
    assert again.status_code == 201, again.text
    assert UUID(again.json()["job_id"]) != job_id
    assert cenv.songenv.row(UUID(again.json()["job_id"])).credit_state == "none"
