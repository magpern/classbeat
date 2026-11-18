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


async def test_the_class_answer_says_composer_and_the_fill_of_its_sketch(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    before = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert (before["composer"], before["fill_songs"]) == (True, None)  # no fill started yet
    made = await cenv.fill(rider, class_id)
    job_id = UUID(made.json()["job_id"])
    started = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert started["fill_songs"] == {
        "sketch_revision_id": str(sketch),
        "attempt": 0,
        "failure": None,
    }
    cenv.sql(
        "UPDATE jobs SET state = 'failed', error_category = 'pool_too_small', "
        "finished_at = now() WHERE id = :j",
        j=job_id,
    )
    failed = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert failed["fill_songs"]["failure"] == "pool_too_small"  # Find songs again is offered
    cenv.sql(
        "UPDATE jobs SET input = jsonb_set(input, '{composer}', 'false') "
        "WHERE class_id = :c AND kind = 'interpret_request'",
        c=class_id,
    )
    other = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert other["composer"] is False


async def test_a_class_of_the_old_flow_is_not_a_composer_class(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    made = await rider.create()
    class_id = made.json()["class_id"]
    answer = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert (answer["composer"], answer["fill_songs"]) == (False, None)


async def test_the_page_names_the_sketch_it_shows(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    path = f"/api/classes/{class_id}/fill-songs"
    stale = await rider.browser.post(path, {"base_revision_id": str(UUID(int=9))})
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "stale_base")
    made = await rider.browser.post(path, {"base_revision_id": str(sketch)})
    assert made.status_code == 201, made.text
    assert set(made.json()) == {"job_id", "kind", "state"}


async def test_a_transient_class_busy_leaves_no_fill_on_the_class_answer(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    interpret = cenv.sql(
        "SELECT id FROM jobs WHERE class_id = :c AND kind = 'interpret_request'", c=class_id
    ).scalar_one()
    cenv.sql("UPDATE jobs SET state = 'queued', finished_at = NULL WHERE id = :j", j=interpret)
    busy = await cenv.fill(rider, class_id)
    assert (busy.status_code, busy.json()["detail"]["reason"]) == (409, "class_busy")
    answer = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert answer["fill_songs"] is None  # the page starts the fill again once the class is free
    cenv.sql("UPDATE jobs SET state = 'completed', finished_at = now() WHERE id = :j", j=interpret)
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
