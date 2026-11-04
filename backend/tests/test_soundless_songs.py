"""Song picking refuses a soundless class (ADR 0007 amendment of 2026-11-03, item 9; SL4, #530),
against Postgres as ``classbeat_app``, the fake Spotify, the fake MOWL and ``FakeProvider``.
Never the network, never a real account or model.

A soundless class's ``music_source`` is ``{"source": "none"}``; these tests set it on a class
the song kinds made (as the superuser), which is exactly the state the explicit checks must catch:
the refusal reads ``classes.music_source``, not the absence of a song list. Covered:
``design_with_songs`` (a pick on the class and a resume) and ``revise_songs`` are refused 409
``invalid_input`` at creation with nothing written; and the two final stores (``songs.phase2``
and ``songs.revise``) roll back ``invalid_input`` when the class turned soundless after admit, so
no draft is stored and ``music_source`` stays ``none``.
"""

import json
from typing import Any
from uuid import UUID

import pytest

from revisesongtools import change, changed_approved, current, picked, resume_change
from songtools import SongEnv, failing_script

pytestmark = pytest.mark.usefixtures("songenv")

NONE = {"source": "none"}


def make_soundless(songenv: SongEnv, class_id: UUID) -> None:
    songenv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=json.dumps(NONE),
        c=class_id,
    )


def klass(songenv: SongEnv, class_id: UUID) -> Any:
    return songenv.sql("SELECT * FROM classes WHERE id = :id", id=class_id).one()


def drafts(songenv: SongEnv, class_id: UUID) -> int:
    return songenv.sql(
        "SELECT count(*) FROM class_revisions WHERE class_id = :c AND kind = 'draft'", c=class_id
    ).scalar_one()


def jobs_of(songenv: SongEnv, owner: UUID) -> int:
    return songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


def refused(response: Any) -> None:
    assert (response.status_code, response.json()["detail"]["reason"]) == (409, "invalid_input")


async def test_a_pick_and_a_resume_on_a_soundless_class_are_refused(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, list_id = await songenv.approved(rider)
    songenv.script = failing_script("auth")  # phase 2 fails after its playlist: a valid receipt
    assert (await songenv.run(job_id)).error_category == "model_config"
    class_id = songenv.row(job_id).class_id
    make_soundless(songenv, class_id)
    before = (jobs_of(songenv, rider.user_id), songenv.counts())
    refused(await rider.create(class_id=str(class_id)))
    refused(await rider.resume(class_id, list_id))
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == before
    # The same class with music again: the resume is admitted (the refusal was the soundless check).
    songenv.sql("UPDATE classes SET music_source = NULL WHERE id = :c", c=class_id)
    assert (await rider.resume(class_id, list_id)).status_code == 201


async def test_a_song_change_of_a_soundless_class_is_refused(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    source = klass(songenv, class_id).music_source
    make_soundless(songenv, class_id)
    before = (jobs_of(songenv, rider.user_id), songenv.counts())
    refused(await change(rider, class_id, base))
    assert (jobs_of(songenv, rider.user_id), songenv.counts()) == before
    songenv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=json.dumps(source),
        c=class_id,
    )
    assert (await change(rider, class_id, base)).status_code == 201


async def test_a_song_change_resume_of_a_soundless_class_is_refused(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, list_id = await changed_approved(songenv, rider, class_id, base)
    songenv.script = failing_script("auth")
    assert (await songenv.run(job_id)).error_category == "model_config"
    source = klass(songenv, class_id).music_source
    make_soundless(songenv, class_id)
    before = (jobs_of(songenv, rider.user_id), songenv.counts(), drafts(songenv, class_id))
    refused(await resume_change(rider, class_id, base, list_id))
    # Nothing written: no job, no credit or row movement, no revision (#587).
    assert (jobs_of(songenv, rider.user_id), songenv.counts(), drafts(songenv, class_id)) == before
    # The control: the same resume on the class with its music again is admitted, so the refusal
    # above was the soundless check and nothing else.
    songenv.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=json.dumps(source),
        c=class_id,
    )
    assert (await resume_change(rider, class_id, base, list_id)).status_code == 201


async def test_the_pick_store_never_writes_over_a_none_source(songenv: SongEnv) -> None:
    """Admitted on a class without a source; soundless by the final transaction: it rolls back."""
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    class_id = songenv.row(job_id).class_id
    make_soundless(songenv, class_id)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    shown = klass(songenv, class_id)
    assert (shown.music_source, shown.current_revision_id, shown.brief) == (NONE, None, None)
    assert drafts(songenv, class_id) == 0


async def test_the_song_change_store_never_writes_over_a_none_source(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    job_id, _ = await changed_approved(songenv, rider, class_id, base)
    make_soundless(songenv, class_id)
    count = drafts(songenv, class_id)
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    shown = klass(songenv, class_id)
    assert (shown.music_source, current(songenv, class_id)) == (NONE, base)
    assert drafts(songenv, class_id) == count
