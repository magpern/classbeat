"""The composer's song fill (ADR 0038 sections 5, 9 and 12, #1188) through the hand-off route and
the worker side, against Postgres, the fake Spotify, the fake MOWL and ``FakeProvider``: the fill
waits on a ``v: 3`` list whose preview is the design, the sketch stays current and no draft is
stored, no credit is used, two calls make one job, a retry and a new sketch use new keys. Never
the network, a real account or a model."""

import asyncio
from uuid import UUID

import pytest

from classbeat.songs import song_list
from composertools import SKETCH, ComposerEnv
from songtools import CHOSEN

pytestmark = pytest.mark.usefixtures("jenv")


async def test_a_fill_waits_on_a_v3_list_and_the_sketch_stays_current(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    content = cenv.content(list_id)
    assert content["v"] == 3
    assert [s["spotify_id"] for s in content["songs"]] == CHOSEN
    preview = content["preview"]
    assert preview["music"]["fingerprint"] == song_list.fingerprint(tuple(CHOSEN))
    assert len(preview["music"]["songs"]) == len(CHOSEN)
    assert preview["content"]["name"] == "Invented Tuesday climb"
    assert song_list.content_hash(content).hex() == digest
    assert cenv.current(class_id) == sketch  # no pointer moved
    assert cenv.drafts(class_id) == 1  # the sketch only: no draft revision
    row = cenv.songenv.row(job_id)
    assert row.credit_state == "none"
    assert cenv.songenv.class_playlists(rider.user_id) == []  # no final playlist before Approve


async def test_two_concurrent_fill_calls_make_one_job(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    first, second = await asyncio.gather(cenv.fill(rider, class_id), cenv.fill(rider, class_id))
    assert sorted([first.status_code, second.status_code]) == [200, 201], (first.text, second.text)
    assert first.json()["job_id"] == second.json()["job_id"]
    count = cenv.sql(
        "SELECT count(*) FROM jobs WHERE class_id = :c AND kind = 'design_with_songs'", c=class_id
    ).scalar_one()
    assert count == 1


async def test_a_retry_and_a_new_sketch_use_new_keys(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    first = UUID(made.json()["job_id"])
    busy = await cenv.fill(rider, class_id, retry=True)
    assert busy.json()["detail"]["reason"] == "class_busy"  # the first fill is still queued
    cenv.sql(
        "UPDATE jobs SET state = 'failed', error_category = 'upstream_unavailable', "
        "finished_at = now() WHERE id = :j",
        j=first,
    )
    retried = await cenv.fill(rider, class_id, retry=True)
    assert retried.status_code == 201, retried.text
    second = UUID(retried.json()["job_id"])
    assert second != first
    again = await cenv.fill(rider, class_id)  # the automatic call answers the retry's job
    assert (again.status_code, UUID(again.json()["job_id"])) == (200, second)
    keys = (
        cenv.sql("SELECT idempotency_key FROM jobs WHERE id = ANY(:j)", j=[first, second])
        .scalars()
        .all()
    )
    assert len(set(keys)) == 2
    view = (await rider.browser.get(f"/api/classes/{class_id}/fill-songs")).json()
    assert (view["sketch_revision_id"], view["attempt"]) == (str(sketch), 1)
    # A new sketch (a shape change before the fill): attempt 0 again, a key of its own.
    cenv.sql("UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :j", j=second)
    newer = UUID(int=sketch.int + 1)
    cenv.insert_sketch(rider.user_id, class_id, newer, {**SKETCH, "name": "Other"}, number=2)
    cenv.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=newer, c=class_id)
    third = await cenv.fill(rider, class_id)
    assert third.status_code == 201, third.text
    assert UUID(third.json()["job_id"]) not in (first, second)
    stored = cenv.sql(
        "SELECT input ->> 'base_revision_id' FROM jobs WHERE id = :j",
        j=UUID(third.json()["job_id"]),
    ).scalar_one()
    assert stored == str(newer)


async def test_the_composer_fields_are_server_fields(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    refused = await rider.create(class_id=str(class_id), mode="fill", base_revision_id=str(sketch))
    assert refused.status_code == 422, refused.text  # POST /api/jobs never makes a composer job
