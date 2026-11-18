"""Approve and save, phase 2 of a composer's list (ADR 0038 section 7, #1189).

On ``composertools``' world (the fake Spotify with an invented catalogue, ``FakeMowlPool``, the real
``design_with_songs`` kind, ``FakeProvider``, Postgres as ``classbeat_app``):

- before the approval nothing is written for the class (no final playlist, no MOWL attach of one;
  the fakes' own logs show only the pool's private working copy, ADR 0008 section 2);
- approved with ``save: true`` and the final songs as approved, phase 2 re-times the preview with
  no model and stores the first real draft (``origin = design_with_songs``, provenance ``fit:
  kept`` naming the preview's design run, ``music.song_list_id``) by compare-and-set from the
  sketch, and ``save_pending`` names it;
- a song out of tolerance or a total drift over 5 seconds re-designs with a model on the approved
  preview, with no ``save_pending`` and the class saying the songs differed;
- a sketch that is no longer current makes ``prepare`` refuse before any Spotify write;
- ``save: true`` is refused for any other job, and a repeated approval never drops it.

Never the network, a real account or a model.
"""

import copy
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest

from composertools import SKETCH, ComposerEnv, change_script
from songtools import Rider

pytestmark = pytest.mark.usefixtures("jenv")


async def approve(
    rider: Rider, job_id: UUID, list_id: UUID, digest: str, *, save: bool = True
) -> httpx.Response:
    body = {"revision_id": str(list_id), "content_hash": digest, "save": save}
    return await rider.browser.post(f"/api/jobs/{job_id}/approve", body)


def turns(cenv: ComposerEnv) -> int:
    return sum(len(p.sent) for p in cenv.songenv.providers)


def final_creates(cenv: ComposerEnv, owner: UUID) -> int:
    rows = cenv.songenv.operations(owner)
    return sum(1 for r in rows if r.idempotency_key.endswith(":final-create"))


async def waiting(cenv: ComposerEnv) -> tuple[Rider, UUID, UUID, UUID, UUID, str]:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    return rider, class_id, sketch, job_id, list_id, digest


def stretch(cenv: ComposerEnv, list_id: UUID, extra_ms: dict[int, int]) -> None:
    """The final playlist's songs (by position in the list) last ``extra_ms`` longer in MOWL's
    analysis than in the pool's: the same tracks in the same order, timed differently."""
    songs = cenv.content(list_id)["songs"]
    for position, extra in extra_ms.items():
        track = cenv.songenv.fake.catalogue[songs[position]["spotify_id"]]
        track["duration_ms"] = int(track.get("duration_ms", 240_000)) + extra


async def test_approve_and_save_keeps_the_preview_and_sets_save_pending(
    cenv: ComposerEnv,
) -> None:
    rider, class_id, sketch, job_id, list_id, digest = await waiting(cenv)
    owner = rider.user_id
    # Before the approval: no final playlist, nothing of the class written anywhere. The fakes'
    # own logs: phase 1's only writes are the pool's private working copy (ADR 0008 section 2).
    assert final_creates(cenv, owner) == 0
    assert cenv.songenv.class_playlists(owner) == []
    spotify_creates, mowl_attaches = cenv.songenv.creates(), len(cenv.songenv.mowl.attaches)
    answer = await approve(rider, job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    assert cenv.songenv.row(job_id).approval_save is True
    assert (cenv.songenv.creates(), len(cenv.songenv.mowl.attaches)) == (
        spotify_creates,
        mowl_attaches,
    )  # the approval itself writes nothing
    before = turns(cenv)
    row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert turns(cenv) == before  # the fit: no model
    assert "fit" in cenv.songenv.stages(job_id)
    assert final_creates(cenv, owner) == 1
    # Phase 2: exactly one more Spotify create (the final playlist) and one more MOWL attach.
    assert cenv.songenv.creates() == spotify_creates + 1
    assert len(cenv.songenv.mowl.attaches) == mowl_attaches + 1
    [playlist] = cenv.songenv.class_playlists(owner)
    assert playlist.state == "verified"

    draft_id = cenv.current(class_id)
    assert draft_id not in (None, sketch)
    draft = cenv.songenv.revision(draft_id)
    preview = cenv.content(list_id)["preview"]
    assert draft.kind == "draft"
    assert draft.provenance["origin"] == "design_with_songs"
    assert draft.provenance["fit"] == "kept"
    assert draft.provenance["agent_run_ids"] == [preview["design_run_id"]]
    assert (
        "base_revision_id" not in draft.provenance or draft.provenance["base_revision_id"] is None
    )
    assert draft.music["song_list_id"] == str(list_id)
    shape = [[i["zone"] for i in s["intervals"]] for s in draft.content["segments"]]
    assert shape == [[i["zone"] for i in s["intervals"]] for s in preview["content"]["segments"]]
    pending = cenv.sql("SELECT save_pending FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert pending == {
        "revision_id": str(draft_id),
        "content_hash": bytes(draft.content_hash).hex(),
        "job_id": str(job_id),
    }
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["save_pending"] == pending
    assert view["phase2"] is None
    assert cenv.songenv.row(job_id).credit_state == "none"
    ledger = cenv.sql("SELECT count(*) FROM credit_ledger WHERE job_id = :j", j=job_id)
    assert ledger.scalar_one() == 0


async def test_an_approval_without_save_stores_the_draft_and_no_save_pending(
    cenv: ComposerEnv,
) -> None:
    rider, class_id, _, job_id, list_id, digest = await waiting(cenv)
    answer = await approve(rider, job_id, list_id, digest, save=False)
    assert answer.status_code == 200, answer.text
    row = await cenv.songenv.run(job_id)
    assert row.state == "completed"
    draft_id = cenv.current(class_id)
    assert draft_id is not None
    draft = cenv.songenv.revision(draft_id)
    assert draft.provenance["fit"] == "kept"
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["save_pending"] is None


DIFFERED = {
    "a song 3 s longer": {2: 3_000},
    "four songs 1.5 s longer each (total 6 s)": {0: 1_500, 1: 1_500, 2: 1_500, 3: 1_500},
}


@pytest.mark.parametrize("extra", list(DIFFERED.values()), ids=list(DIFFERED))
async def test_final_songs_that_differ_are_re_designed_with_no_save_pending(
    cenv: ComposerEnv, extra: dict[int, int]
) -> None:
    rider, class_id, sketch, job_id, list_id, digest = await waiting(cenv)
    preview = cenv.content(list_id)["preview"]
    answer = await approve(rider, job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    stretch(cenv, list_id, extra)
    cenv.songenv.script = change_script()  # the design stage on the approved preview
    before = turns(cenv)
    row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert turns(cenv) > before  # a model designed it
    draft_id = cenv.current(class_id)
    assert draft_id not in (None, sketch)
    draft = cenv.songenv.revision(draft_id)
    assert draft.provenance["origin"] == "design_with_songs"
    assert draft.provenance["fit"] == "redesigned"
    assert draft.provenance["agent_run_ids"] != [preview["design_run_id"]]
    assert draft.music["song_list_id"] == str(list_id)
    pending = cenv.sql("SELECT save_pending FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert pending is None
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["save_pending"] is None
    assert view["phase2"] == {"state": "differed"}


UNPROVEN = {
    "the design run is gone": "DELETE FROM agent_runs WHERE id = :r",
    "the design run is another job's": (
        "UPDATE agent_runs SET job_id = (SELECT j.id FROM jobs j WHERE j.class_id = :c "
        "AND j.kind = 'interpret_request') WHERE id = :r"
    ),
}


@pytest.mark.parametrize("statement", list(UNPROVEN.values()), ids=list(UNPROVEN))
async def test_a_design_run_that_cannot_be_proven_is_re_designed_with_no_save_pending(
    cenv: ComposerEnv, statement: str
) -> None:
    rider, class_id, _, job_id, list_id, digest = await waiting(cenv)
    preview = cenv.content(list_id)["preview"]
    answer = await approve(rider, job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    cenv.sql(statement, r=UUID(preview["design_run_id"]), c=class_id)
    cenv.songenv.script = change_script()
    row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    draft_id = cenv.current(class_id)
    assert draft_id is not None
    draft = cenv.songenv.revision(draft_id)
    assert draft.provenance["fit"] == "redesigned"
    assert preview["design_run_id"] not in draft.provenance["agent_run_ids"]
    pending = cenv.sql("SELECT save_pending FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert pending is None


async def test_a_sketch_that_is_no_longer_current_makes_prepare_refuse(
    cenv: ComposerEnv,
) -> None:
    rider, class_id, _, job_id, list_id, digest = await waiting(cenv)
    answer = await approve(rider, job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    other = uuid.uuid4()
    cenv.insert_sketch(rider.user_id, class_id, other, copy.deepcopy(SKETCH), number=99)
    cenv.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=other, c=class_id)
    row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert final_creates(cenv, rider.user_id) == 0  # refused before any Spotify write
    assert cenv.songenv.class_playlists(rider.user_id) == []
    assert cenv.current(class_id) == other
    assert cenv.drafts(class_id) == 2  # the two sketches: no draft stored


async def test_save_is_refused_for_any_other_job(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    job_id, list_id, digest = await cenv.songenv.waiting(rider)  # an ordinary pick
    answer = await approve(rider, job_id, list_id, digest)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "invalid_input"
    row = cenv.songenv.row(job_id)
    assert (row.state, row.approved_at, row.approval_save) == ("waiting_for_approval", None, False)
    plain = await approve(rider, job_id, list_id, digest, save=False)
    assert plain.status_code == 200, plain.text  # an approval without save behaves as today


async def test_a_repeated_approval_never_drops_or_adds_the_save(cenv: ComposerEnv) -> None:
    rider, _, _, job_id, list_id, digest = await waiting(cenv)
    first = await approve(rider, job_id, list_id, digest)
    assert first.status_code == 200, first.text
    same = await approve(rider, job_id, list_id, digest)
    assert same.status_code == 200, same.text  # the same click again: a no-op
    other = await approve(rider, job_id, list_id, digest, save=False)
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["reason"] == "not_waiting"
    assert cenv.songenv.row(job_id).approval_save is True


async def test_a_browser_cannot_set_the_resume_fields(cenv: ComposerEnv) -> None:
    rider, class_id, sketch, _, list_id, _ = await waiting(cenv)
    extra: dict[str, Any] = {"base_revision_id": str(sketch), "save": True}
    answer = await rider.create(
        class_id=str(class_id),
        mode="resume",
        resume={"song_list_revision_id": str(list_id)},
        **extra,
    )
    assert answer.status_code == 422, answer.text  # server fields: never from the browser
