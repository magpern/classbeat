"""A class's music kind never changes, in both directions, for the SwedeBeat studio (ADR 0036
section 2; SR C3): a studio class refuses ``design_with_songs``, ``revise_songs``, a design and
every other music kind's job, and ``studio_songs`` refuses a class with another kind's music,
soundless included, at admit, in the worker and in the final store. Fakes only: the fake Spotify,
MOWL and SwedeBeat gateway and ``FakeProvider``; no network, no real account."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.db import user_session
from classbeat.design import music_kind
from classbeat.jobs.kinds import Refused
from ctx import make_context
from revisesongtools import change, picked
from songtools import SongEnv
from studiotools import StudioEnv

STUDIO = {"source": "swedebeat_studio"}
OTHER = {"source": "mowl_spotify_playlist", "origin": "song_pick"}
NONE = {"source": "none"}


def _source(songs: SongEnv, class_id: UUID, value: Any) -> None:
    songs.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=None if value is None else json.dumps(value),
        c=class_id,
    )


def _refused(response: Any) -> None:
    assert (response.status_code, response.json()["detail"]["reason"]) == (409, "invalid_input")


def _jobs(songs: SongEnv, owner: UUID) -> int:
    return songs.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


async def test_a_studio_source_refuses_a_song_pick_and_a_song_change(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    stored = songenv.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar()
    _source(songenv, class_id, STUDIO)
    before = (_jobs(songenv, rider.user_id), songenv.counts())
    _refused(await rider.create(class_id=str(class_id)))  # design_with_songs on the class
    _refused(await change(rider, class_id, base))  # revise_songs
    assert (_jobs(songenv, rider.user_id), songenv.counts()) == before
    # The control: with its own music again the change is admitted (the refusal was the lock).
    _source(songenv, class_id, stored)
    assert (await change(rider, class_id, base)).status_code == 201


def _cancel(studio: StudioEnv, job_id: UUID) -> None:
    """The first list's job ended (the user's Cancel), so the class takes another pick."""
    studio.songs.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :id", id=job_id
    )


async def _studio_class(studio: StudioEnv) -> tuple[Any, UUID, UUID]:
    studio.found(*range(14))
    rider = await studio.rider()
    job_id, _, _ = await studio.waiting(rider)
    return rider, job_id, studio.songs.row(job_id).class_id


async def test_a_studio_list_makes_a_studio_class_for_every_other_kinds_check(
    studio: StudioEnv,
) -> None:
    """No stored source yet: the newest revision (the studio list) decides, for the checks of
    ``design_draft`` (both kinds), ``design_with_songs``, ``revise_songs``, ``swedebeat_match``,
    the hand-off and ``swedebeat_verify_mix``; only the studio's own admit passes."""
    rider, _, class_id = await _studio_class(studio)
    assert (
        studio.songs.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar()
        is None
    )
    async with user_session(studio.songs.factory, make_context(rider.user_id)) as db:
        for soundless in (False, True):
            refused = await music_kind.admit_music(db, rider.user_id, soundless, class_id)
            assert refused == Refused("invalid_input")
        assert await music_kind.refuse_soundless(db, rider.user_id, class_id) == Refused(
            "invalid_input"
        )
        assert await music_kind.admit_studio(db, rider.user_id, class_id) is None


@pytest.mark.parametrize("value", [OTHER, NONE], ids=["other_music", "soundless"])
async def test_a_studio_job_refuses_a_class_of_another_kind(studio: StudioEnv, value: Any) -> None:
    rider, job_id, class_id = await _studio_class(studio)
    _cancel(studio, job_id)
    _source(studio.songs, class_id, value)
    before = _jobs(studio.songs, rider.user_id)
    _refused(await rider.create(class_id=str(class_id)))
    assert _jobs(studio.songs, rider.user_id) == before
    # The control: with the studio's source the same pick is admitted.
    _source(studio.songs, class_id, STUDIO)
    assert (await rider.create(class_id=str(class_id))).status_code == 201


async def test_a_class_of_another_kind_by_the_worker_gets_no_match(studio: StudioEnv) -> None:
    rider, job_id, class_id = await _studio_class(studio)
    _cancel(studio, job_id)
    queued = await studio.created(rider, class_id=str(class_id))
    _source(studio.songs, class_id, OTHER)
    calls = len(studio.matches())
    row = await studio.run(queued)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(studio.matches()) == calls  # refused in prepare, before any outside call


async def test_a_song_list_of_another_kind_landing_first_rolls_the_studio_store_back(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admitted on a class with no music; a revision of other music lands before the store."""
    rider, job_id, class_id = await _studio_class(studio)
    _cancel(studio, job_id)
    queued = await studio.created(rider, class_id=str(class_id))
    lists = studio.songs.sql(
        "SELECT count(*) FROM class_revisions WHERE class_id = :c", c=class_id
    ).scalar_one()
    # Every revision of the class turns to other music, after the worker's prepare has read it.
    original = music_kind.still_studio

    async def then_other(fs: Any, cid: UUID) -> None:
        await original(fs, cid)
        studio.songs.sql(
            "UPDATE class_revisions SET music = CAST(:m AS jsonb) WHERE class_id = :c",
            m=json.dumps(OTHER),
            c=class_id,
        )

    monkeypatch.setattr(music_kind, "still_studio", then_other)
    calls = len(studio.matches())
    row = await studio.run(queued)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert len(studio.matches()) > calls  # it got as far as the store, which refused
    assert (
        studio.songs.sql(
            "SELECT count(*) FROM class_revisions WHERE class_id = :c", c=class_id
        ).scalar_one()
        == lists
    )  # nothing stored


async def test_a_studio_revision_alone_rolls_another_kinds_final_store_back(
    songenv: SongEnv,
) -> None:
    """The reverse store lock: the class has no stored source and its song list is untouched;
    only one revision of studio music (planted after the approval, as a job of the other kind
    landing first would leave it) makes it a studio class, so ``design_with_songs``' final store
    (``check_stored``) refuses and nothing is written."""
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider)
    class_id = songenv.row(job_id).class_id
    assert (
        songenv.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar() is None
    )
    songenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, job_id) SELECT gen_random_uuid(), owner_id, class_id, "
        "(SELECT max(number) FROM class_revisions WHERE class_id = :c) + 1, 'swedebeat_match', "
        "'{}'::jsonb, sha256('planted'::bytea), CAST(:m AS jsonb), '{}'::jsonb, id "
        "FROM jobs WHERE id = :j",
        c=class_id,
        m=json.dumps(STUDIO),
        j=job_id,
    )
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert (
        songenv.sql(
            "SELECT count(*) FROM class_revisions WHERE class_id = :c AND kind = 'draft'",
            c=class_id,
        ).scalar_one()
        == 0
    )
    assert (
        songenv.sql("SELECT music_source FROM classes WHERE id = :c", c=class_id).scalar() is None
    )
