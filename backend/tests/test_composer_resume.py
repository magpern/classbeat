"""**Create the playlist again** after a composer's phase 2 failed (ADR 0038 section 7, #1189;
``classbeat.classes.song_resume``).

- The class says ``phase2: {state: "failed"}`` while the receipt can be resumed.
- The resume hand-off reads the approved list, its sketch and ``save`` from the receipt; a body the
  browser sends (another list, another hash) changes nothing. It takes no credit, runs ``fit`` and
  sets ``save_pending`` for its own job.
- ``POST /api/jobs`` cannot resume a composer's list (its job would reserve a credit).
- After a revise of the sketch the resume is refused ``stale_base`` and the class stops offering it.

On ``composertools``' world; phase 2's first attempt is failed through the kind's
``composer_phase_two`` seam. Never the network, a real account or a model.
"""

import copy
import secrets
import uuid
from uuid import UUID

import httpx
import pytest

from classbeat.jobs import JobFailure, JobRun
from classbeat.songs import kind as song_kind
from classbeat.songs.inputs import DesignWithSongsInput
from composertools import SKETCH, ComposerEnv
from songtools import Rider

pytestmark = pytest.mark.usefixtures("jenv")


async def _mismatch(run: JobRun, given: DesignWithSongsInput, runtime: object) -> object:
    raise JobFailure("playlist_mismatch")


async def failed_phase_two(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> tuple[Rider, UUID, UUID, UUID, UUID]:
    """A composer list approved with ``save: true`` whose phase 2 failed: (rider, class, sketch,
    the approved job, the list)."""
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    body = {"revision_id": str(list_id), "content_hash": digest, "save": True}
    answer = await rider.browser.post(f"/api/jobs/{job_id}/approve", body)
    assert answer.status_code == 200, answer.text
    with monkeypatch.context() as patched:
        patched.setattr(song_kind, "composer_phase_two", _mismatch)
        row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "playlist_mismatch")
    return rider, class_id, sketch, job_id, list_id


async def resume(rider: Rider, class_id: UUID, body: object = None) -> httpx.Response:
    key = {"Idempotency-Key": secrets.token_urlsafe(18)}
    return await rider.browser.post(f"/api/classes/{class_id}/resume-songs", body, key)


async def test_the_resume_reads_the_receipt_takes_no_credit_and_sets_save_pending(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, sketch, _, list_id = await failed_phase_two(cenv, monkeypatch)
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["phase2"] == {"state": "failed"}
    assert view["songs"]["state"] == "approved"
    assert view["fill_songs"]["sketch_revision_id"] == str(sketch)
    # The browser names another list, hash, base and save: all of it is ignored.
    planted = {
        "song_list_revision_id": str(uuid.uuid4()),
        "content_hash": "0" * 64,
        "base_revision_id": str(uuid.uuid4()),
        "save": False,
    }
    made = await resume(rider, class_id, planted)
    assert made.status_code == 201, made.text
    resumed = UUID(made.json()["job_id"])
    row = cenv.songenv.row(resumed)
    assert row.input["mode"] == "resume"
    assert row.input["resume"] == {"song_list_revision_id": str(list_id)}
    assert row.input["base_revision_id"] == str(sketch)
    assert row.input["save"] is True
    assert row.credit_state == "none"
    ledger = cenv.sql("SELECT count(*) FROM credit_ledger WHERE job_id = :j", j=resumed)
    assert ledger.scalar_one() == 0  # no reservation, no second credit
    done = await cenv.songenv.run(resumed)
    assert (done.state, done.error_category) == ("completed", None)
    draft_id = cenv.current(class_id)
    assert draft_id is not None
    draft = cenv.songenv.revision(draft_id)
    assert (draft.provenance["origin"], draft.provenance["fit"]) == ("design_with_songs", "kept")
    preview = cenv.content(list_id)["preview"]
    assert draft.provenance["agent_run_ids"] == [preview["design_run_id"]]  # the approved job's
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["save_pending"] == {
        "revision_id": str(draft_id),
        "content_hash": bytes(draft.content_hash).hex(),
        "job_id": str(resumed),
    }
    assert view["phase2"] is None
    again = await resume(rider, class_id)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["reason"] == "receipt_invalid"  # spent once its list is done


async def test_the_same_key_is_the_same_resume(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, *_ = await failed_phase_two(cenv, monkeypatch)
    key = {"Idempotency-Key": secrets.token_urlsafe(18)}
    path = f"/api/classes/{class_id}/resume-songs"
    first = await rider.browser.post(path, None, key)
    second = await rider.browser.post(path, None, key)
    assert (first.status_code, second.status_code) == (201, 200), second.text
    assert first.json()["job_id"] == second.json()["job_id"]


async def test_the_jobs_api_cannot_resume_a_composer_list(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, _, list_id = await failed_phase_two(cenv, monkeypatch)
    answer = await rider.resume(class_id, list_id)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "receipt_invalid"


async def test_after_a_revise_of_the_sketch_the_resume_is_stale(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _, _, _ = await failed_phase_two(cenv, monkeypatch)
    revised = uuid.uuid4()
    cenv.insert_sketch(rider.user_id, class_id, revised, copy.deepcopy(SKETCH), number=99)
    cenv.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=revised, c=class_id)
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["phase2"] is None  # the chip stops offering Create the playlist again
    answer = await resume(rider, class_id)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "stale_base"


async def test_a_list_that_was_never_approved_has_no_resume(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    await cenv.waiting(rider, class_id)
    answer = await resume(rider, class_id)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "receipt_invalid"
