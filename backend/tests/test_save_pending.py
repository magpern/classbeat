"""``save_pending`` and the save route (ADR 0038 section 7, #1189; ``saving.save_pending``).

On ``savetools``' world (the real jobs API, the real ``save_workout`` and ``update_workout`` kinds,
the in-process fake MOWL that keeps programs and counts every write): a class whose current draft
is the one ``save_pending`` names, as a composer's phase 2 leaves it.

- With ``save_pending_job_id`` the save admit checks the revision, the hash and the job against
  ``save_pending`` (``not_pending`` otherwise); without it a manual Save is unaffected.
- An admitted save or update clears it, so the driver and a manual Save never both save; a
  failed save leaves it cleared and nothing saves again by itself.
- A newer revision voids it (current or not), and a pointer moved back to it does not revive it.
- Two concurrent hand-offs (the same key; the driver against a manual Save) make one save job and
  one MOWL write.

Never the network.
"""

import asyncio
import json
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest

from savetools import WRITES, FakeMowlPrograms, Saved, SaveEnv

pytestmark = pytest.mark.usefixtures("jenv")


def plant_pending(senv: SaveEnv, saved: Saved, job_id: UUID | None = None) -> UUID:
    """The class's current revision is ``saved``'s, and ``save_pending`` names it."""
    job = job_id or uuid.uuid4()
    pending = {
        "revision_id": str(saved.revision_id),
        "content_hash": saved.hash_hex,
        "job_id": str(job),
    }
    senv.sql(
        "UPDATE classes SET current_revision_id = :r, save_pending = CAST(:p AS jsonb) "
        "WHERE id = :c",
        r=saved.revision_id,
        p=json.dumps(pending),
        c=saved.class_id,
    )
    return job


def pending_of(senv: SaveEnv, saved: Saved) -> Any:
    return senv.sql("SELECT save_pending FROM classes WHERE id = :c", c=saved.class_id).scalar_one()


def reason(answer: httpx.Response) -> str:
    assert answer.status_code == 409, answer.text
    return answer.json()["detail"]["reason"]


async def test_the_driver_saves_exactly_save_pending_and_clears_it(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    job = plant_pending(senv, saved)
    browser = await save_browser(saved)
    answer = await browser.save(save_pending_job_id=str(job))
    assert answer.status_code == 201, answer.text
    assert pending_of(senv, saved) is None  # cleared in the admit's transaction
    await senv.run_next()
    assert senv.row(UUID(answer.json()["id"])).state == "completed"
    assert len(fake_programs.writes) == WRITES  # one save
    again = await browser.save(save_pending_job_id=str(job))
    assert reason(again) == "not_pending"  # nothing saves twice
    assert len(fake_programs.writes) == WRITES


@pytest.mark.parametrize("wrong", ["revision", "hash", "job"])
async def test_the_driver_is_refused_another_revision_hash_or_job(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any, wrong: str
) -> None:
    older = await senv.saved_class()
    newer_id = senv.plant_revision(older.owner, older.class_id, older.link_id)
    saved = Saved(
        older.owner, older.connection, older.class_id, newer_id, older.link_id, older.content
    )
    job = plant_pending(senv, saved)  # the newer one, current
    overrides: dict[str, Any] = {"save_pending_job_id": str(job)}
    if wrong == "revision":
        overrides["revision_id"] = str(older.revision_id)  # a real draft of the class
    elif wrong == "hash":
        overrides["content_hash"] = "0" * 64
    else:
        overrides["save_pending_job_id"] = str(uuid.uuid4())
    browser = await save_browser(saved)
    answer = await browser.save(**overrides)
    assert reason(answer) == "not_pending"
    count = "SELECT count(*) FROM jobs WHERE class_id = :c AND kind = 'save_workout'"
    assert senv.sql(count, c=saved.class_id).scalar_one() == 0
    assert pending_of(senv, saved) is not None  # a refusal clears nothing
    assert fake_programs.writes == []


async def test_a_manual_save_is_unaffected_and_clears_it_so_the_driver_never_saves_too(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    job = plant_pending(senv, saved)
    browser = await save_browser(saved)
    manual = await browser.save()  # no save_pending_job_id: no check
    assert manual.status_code == 201, manual.text
    assert pending_of(senv, saved) is None
    busy = await browser.save(save_pending_job_id=str(job))
    assert reason(busy) == "class_busy"  # the manual save is the class's open job
    await senv.run_next()
    after = await browser.save(save_pending_job_id=str(job))
    assert reason(after) == "not_pending"
    assert len(fake_programs.writes) == WRITES


async def test_a_manual_save_without_save_pending_is_as_before(
    senv: SaveEnv, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 201, answer.text
    driver = await browser.save(save_pending_job_id=str(uuid.uuid4()))
    assert reason(driver) in ("class_busy", "not_pending")


async def test_an_admitted_update_clears_it(senv: SaveEnv, save_browser: Any) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    first = await browser.save()
    assert first.status_code == 201, first.text
    await senv.run_next()
    [row] = senv.saved_rows(saved.owner)
    plant_pending(senv, saved)
    body = {
        "kind": "update_workout",
        "input": saved.input(saved_workout_id=str(row.id)),
    }
    answer = await browser.browser.post("/api/jobs", body, {"Idempotency-Key": uuid.uuid4().hex})
    assert answer.status_code == 201, answer.text
    assert pending_of(senv, saved) is None


async def test_a_failed_save_leaves_it_cleared_and_nothing_saves_again(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    job = plant_pending(senv, saved)
    browser = await save_browser(saved)
    answer = await browser.save(save_pending_job_id=str(job))
    assert answer.status_code == 201, answer.text
    # The worker finds the link gone (a MOWL reconnect): the save fails before any write.
    senv.sql("DELETE FROM mowl_playlist_links WHERE id = :l", l=saved.link_id)
    await senv.run_next()
    row = senv.row(UUID(answer.json()["id"]))
    assert row.state == "failed"
    assert pending_of(senv, saved) is None
    assert fake_programs.writes == []
    view = (await browser.browser.get(f"/api/classes/{saved.class_id}")).json()
    assert view["save_pending"] is None  # the page shows the ordinary Save, never "Saving…"


async def test_a_newer_revision_voids_it_even_when_the_pointer_comes_back(
    senv: SaveEnv, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    job = plant_pending(senv, saved)
    browser = await save_browser(saved)
    view = (await browser.browser.get(f"/api/classes/{saved.class_id}")).json()
    assert view["save_pending"] == {
        "revision_id": str(saved.revision_id),
        "content_hash": saved.hash_hex,
        "job_id": str(job),
    }
    newer = senv.plant_revision(saved.owner, saved.class_id, saved.link_id)
    view = (await browser.browser.get(f"/api/classes/{saved.class_id}")).json()
    assert view["save_pending"] is None  # newer, though not current
    move = "UPDATE classes SET current_revision_id = :r WHERE id = :c"
    senv.sql(move, r=newer, c=saved.class_id)
    senv.sql(move, r=saved.revision_id, c=saved.class_id)  # the pointer moved back to it
    view = (await browser.browser.get(f"/api/classes/{saved.class_id}")).json()
    assert view["save_pending"] is None
    assert reason(await browser.save(save_pending_job_id=str(job))) == "not_pending"


async def test_two_concurrent_hand_offs_with_one_key_make_one_job_and_one_write(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    job = plant_pending(senv, saved)
    one, two = await save_browser(saved), await save_browser(saved)
    key = f"save-pending-{job}"
    answers = await asyncio.gather(
        one.save(key, save_pending_job_id=str(job)), two.save(key, save_pending_job_id=str(job))
    )
    assert sorted(a.status_code for a in answers) == [200, 201], [a.text for a in answers]
    assert len({a.json()["id"] for a in answers}) == 1
    await senv.run_next()
    assert len(fake_programs.writes) == WRITES


async def test_the_driver_against_a_manual_save_makes_one_job_and_one_write(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    job = plant_pending(senv, saved)
    one, two = await save_browser(saved), await save_browser(saved)
    answers = await asyncio.gather(one.save(save_pending_job_id=str(job)), two.save())
    created = [a for a in answers if a.status_code == 201]
    assert len(created) == 1, [a.text for a in answers]
    [refused] = [a for a in answers if a.status_code != 201]
    assert reason(refused) in ("class_busy", "not_pending")
    await senv.run_next()
    assert len(fake_programs.writes) == WRITES
    assert pending_of(senv, saved) is None
