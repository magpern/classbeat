"""Resuming an approved song list (ADR 0008 section 6; issue SJ2), and an uncertain final write.

A resume is a new job (``mode = "resume"``) naming the class's approved list. Its receipt is an
approved job of that list that ended without completing; it is spent once a job for that list
completes and refused while another job for it is queued or running. A refused resume creates no
job, reserves no credit and reaches no fake. Fakes only.
"""

import json
import uuid
from typing import Any
from uuid import UUID

import pytest

from songtools import CHOSEN, Rider, SongEnv, design_script, failing_script

pytestmark = pytest.mark.usefixtures("songenv")

MINE = "/v1/me/playlists"
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
OPS = "/api/external-operations"


def refused(response: Any, reason: str, status: int = 409) -> None:
    assert (response.status_code, response.json()["detail"]["reason"]) == (status, reason)


def jobs_of(songenv: SongEnv, owner: UUID) -> int:
    return songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


async def refused_quietly(
    songenv: SongEnv, rider: Rider, class_id: UUID, revision: UUID, reason: str = "receipt_invalid"
) -> None:
    """The resume is refused: no job, no credit, no request at any fake."""
    jobs, counts = jobs_of(songenv, rider.user_id), songenv.counts()
    refused(await rider.resume(class_id, revision), reason)
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == (jobs, counts)


async def failed_after_approval(songenv: SongEnv, rider: Rider) -> tuple[UUID, UUID, UUID]:
    """An approved pick whose phase 2 failed in the design stage (the playlist verified):
    (job id, class id, revision id)."""
    job_id, revision_id = await songenv.approved(rider)
    songenv.script = failing_script("auth")  # model_config: not retried
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    songenv.script = design_script()
    return job_id, row.class_id, revision_id


async def resumed(songenv: SongEnv, rider: Rider, class_id: UUID, revision: UUID) -> Any:
    answer = await rider.resume(class_id, revision)
    assert answer.status_code == 201, answer.text
    resume_id = UUID(answer.json()["id"])
    assert songenv.row(resume_id).credit_state == "reserved"  # the credit is reserved again
    return await songenv.run(resume_id)


async def test_a_resume_reuses_the_verified_playlist_and_then_the_receipt_is_spent(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    _, class_id, revision = await failed_after_approval(songenv, rider)
    [final] = songenv.class_playlists(rider.user_id)
    writes = songenv.writes()
    row = await resumed(songenv, rider, class_id, revision)
    assert (row.state, row.error_category, row.class_id) == ("completed", None, class_id)
    assert songenv.writes() == writes  # no second playlist, no write
    shown = songenv.sql("SELECT * FROM classes WHERE id = :id", id=class_id).one()
    assert shown.music_source["class_playlist_id"] == str(final.id)
    assert songenv.revision(shown.current_revision_id).music["song_list_id"] == str(revision)
    # Spent: a second resume against the original failed job is refused and costs nothing.
    await refused_quietly(songenv, rider, class_id, revision)


async def test_a_resume_in_flight_blocks_another_and_a_failed_one_does_not(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    _, class_id, revision = await failed_after_approval(songenv, rider)
    first = await rider.resume(class_id, revision)
    assert first.status_code == 201, first.text
    # The first one is queued: the class's open job refuses it before the receipt is read.
    await refused_quietly(songenv, rider, class_id, revision, "class_busy")
    songenv.script = failing_script("auth")
    row = await songenv.run(UUID(first.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "model_config")
    songenv.script = design_script()
    assert (await resumed(songenv, rider, class_id, revision)).state == "completed"


async def test_a_list_never_approved_superseded_or_completed_has_no_receipt(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    # Never approved: a list whose wait was cancelled, and one whose wait expired.
    job_id, revision, _ = await songenv.waiting(rider)
    class_id = songenv.row(job_id).class_id
    await refused_quietly(songenv, rider, class_id, revision, "class_busy")  # still waiting
    await rider.browser.post(f"/api/jobs/{job_id}/cancel")
    await refused_quietly(songenv, rider, class_id, revision)
    other, other_revision, _ = await songenv.waiting(rider)
    songenv.sql("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id = :i", i=other)
    assert songenv.sql("SELECT public.queue_expire(10)").scalar_one() == 1
    await refused_quietly(songenv, rider, songenv.row(other).class_id, other_revision)
    # Superseded: the class has a newer list than the approved one.
    _, failed_class, approved = await failed_after_approval(songenv, rider)
    newer = uuid.uuid4()
    songenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) VALUES (:id, :o, :c, 99, 'song_list', "
        "CAST(:content AS jsonb), decode(repeat('ab', 32), 'hex'), '{}', '{}')",
        id=newer, o=rider.user_id, c=failed_class, content=json.dumps({"songs": []}),
    )  # fmt: skip
    await refused_quietly(songenv, rider, failed_class, approved)
    # Completed: the approved phase itself completed.
    done, done_revision = await songenv.approved(rider)
    assert (await songenv.run(done)).state == "completed"
    await refused_quietly(songenv, rider, songenv.row(done).class_id, done_revision)


@pytest.mark.parametrize("write", ["create", "fill"])
async def test_an_uncertain_final_write_is_the_users_and_a_resume_designs_after_it(
    songenv: SongEnv, write: str
) -> None:
    rider = await songenv.rider()
    job_id, revision = await songenv.approved(rider)
    if write == "create":  # applied, no answer, and the listing cannot tell
        songenv.fake.inject("drop", method="POST", path=MINE)
        songenv.fake.inject("status", method="GET", path=MINE, status=500)
    else:  # applied, no answer, and the read-back cannot tell
        songenv.fake.inject("drop", method="PUT", path=ITEMS)
        songenv.fake.inject("status", method="GET", path=ITEMS, status=500, times=2)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    kind = "spotify_create_playlist" if write == "create" else "spotify_replace_items"
    listed = (await rider.browser.get(OPS)).json()["operations"]
    assert [(o["kind"], o["class_id"], o["state"]) for o in listed] == [
        (kind, str(row.class_id), "uncertain")
    ]
    anyway = await rider.browser.post(f"{OPS}/{listed[0]['id']}/do-it-anyway")
    assert anyway.status_code == 200, anyway.text
    creates = songenv.creates()
    done = await resumed(songenv, rider, row.class_id, revision)
    assert (done.state, done.error_category) == ("completed", None)
    assert songenv.creates() == creates + 1  # created once more, under the resume's own key
    final = songenv.class_playlists(rider.user_id)[-1]
    assert final.state == "verified"
    assert songenv.fake.playlists[final.playlist_id].entries == [
        f"spotify:track:{i}" for i in CHOSEN
    ]
