"""A start-screen class whose first creation fails is discarded (ADR 0038), end to end: the jobs
API, the worker side with the fake MOWL and ``FakeProvider`` models, the class and job pages' reads.
Never the network, a real account or a model.

- A failed reading and a failed first design leave no class; the ended job stays readable with
  its category and the class id it had, and ``GET /api/classes/{id}`` answers ``class_discarded``
  with that job (another user still gets a plain ``not_found``).
- The same request with the same idempotency key starts a new class instead of replaying the
  discarded one's job.
- A queued reading the user stops is discarded in the cancel's own transaction.
"""

from typing import Any
from uuid import UUID

from classbeat.design import INTERPRET_REQUEST
from designtools import PLAYLIST
from interprettools import REQUEST, InterpretEnv, ienv, refusing
from jobapi import new_key

__all__ = ["ienv"]  # fixture


def _classes(ienv: InterpretEnv) -> int:
    return ienv.sql("SELECT count(*) FROM classes WHERE owner_id = :o", o=ienv.owner).scalar_one()


async def _start(ienv: InterpretEnv, key: str) -> Any:
    values = {
        "request": REQUEST,
        "mowl_connection_id": str(ienv.renv.browser.connection_id),
        "spotify_connection_id": str(await ienv.connect_spotify()),
        "music_override": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
    }
    body = {"kind": INTERPRET_REQUEST.name, "input": values}
    return await ienv.renv.browser.post("/api/jobs", body, {"Idempotency-Key": key})


async def _gone(ienv: InterpretEnv, class_id: UUID, job_id: UUID) -> None:
    """The class is gone; the page is told which ended job discarded it."""
    page = await ienv.renv.browser.get(f"/api/classes/{class_id}")
    assert page.status_code == 404
    assert page.json() == {"detail": {"reason": "class_discarded", "job_id": str(job_id)}}
    assert ienv.sql("SELECT 1 FROM classes WHERE id = :c", c=class_id).first() is None


async def test_a_failed_reading_discards_its_class_and_stays_readable(ienv: InterpretEnv) -> None:
    ienv.models(refusing())
    made = await ienv.start()
    job_id, class_id = UUID(made.json()["id"]), UUID(made.json()["class_id"])
    await ienv.run(INTERPRET_REQUEST.name)
    await _gone(ienv, class_id, job_id)
    shown = (await ienv.renv.browser.get(f"/api/jobs/{job_id}")).json()
    assert (shown["state"], shown["error_category"]) == ("failed", "model_refused")
    assert shown["class_id"] == str(class_id)  # "Try again" finds the request kept for it
    row = ienv.renv.denv.ops.row(job_id)
    assert (row.class_id, row.discarded_class_id, row.input) == (None, class_id, {})
    assert ienv.credits() == 0
    listed = (await ienv.renv.browser.get("/api/classes")).json()["classes"]
    assert str(class_id) not in {c["id"] for c in listed}


async def test_another_user_gets_a_plain_not_found(ienv: InterpretEnv, browsers: Any) -> None:
    ienv.models(refusing())
    made = await ienv.start()
    class_id = made.json()["class_id"]
    await ienv.run(INTERPRET_REQUEST.name)
    other = await browsers(mowl=False)
    page = await other.get(f"/api/classes/{class_id}")
    assert (page.status_code, page.json()) == (404, {"detail": {"reason": "not_found"}})
    job = await other.get(f"/api/jobs/{made.json()['id']}")
    assert job.status_code == 404


async def test_the_same_key_after_a_discard_starts_a_new_class(ienv: InterpretEnv) -> None:
    ienv.models(refusing())
    key = new_key()
    first = await _start(ienv, key)
    assert first.status_code == 201, first.text
    await ienv.run(INTERPRET_REQUEST.name)
    again = await _start(ienv, key)
    assert again.status_code == 201, again.text  # created, not the discarded class's replay
    assert again.json()["id"] != first.json()["id"]
    assert again.json()["class_id"] != first.json()["class_id"]
    assert ienv.sql("SELECT 1 FROM classes WHERE id = :c", c=again.json()["class_id"]).first()
    replay = await _start(ienv, key)  # the new job is replayed as usual while it lives
    assert (replay.status_code, replay.json()["id"]) == (200, again.json()["id"])


async def test_a_failed_first_design_discards_the_class(ienv: InterpretEnv) -> None:
    class_id, reading_id = await ienv.interpreted()
    handed = await ienv.handoff(class_id)
    assert handed.status_code == 201, handed.text
    design_id = UUID(handed.json()["job_id"])
    ienv.models(refusing())
    await ienv.run("design_draft")
    row = ienv.renv.denv.ops.row(design_id)
    assert (row.state, row.class_id, row.discarded_class_id) == ("failed", None, class_id)
    await _gone(ienv, class_id, design_id)
    assert ienv.sql("SELECT 1 FROM jobs WHERE id = :j", j=reading_id).first() is None
    # The credit was reserved by the design and is released by the settle step, as for any end.
    assert row.credit_state == "reserved"


async def test_a_stopped_queued_reading_is_discarded_by_the_cancel(ienv: InterpretEnv) -> None:
    made = await ienv.start()
    job_id, class_id = UUID(made.json()["id"]), UUID(made.json()["class_id"])
    before = _classes(ienv)
    stopped = await ienv.renv.browser.post(f"/api/jobs/{job_id}/cancel")
    assert stopped.status_code == 200, stopped.text
    assert (stopped.json()["state"], stopped.json()["class_id"]) == ("cancelled", str(class_id))
    await _gone(ienv, class_id, job_id)
    assert _classes(ienv) == before - 1
    repeat = await ienv.renv.browser.post(f"/api/jobs/{job_id}/cancel")
    assert (repeat.status_code, repeat.json()["state"]) == (200, "cancelled")
