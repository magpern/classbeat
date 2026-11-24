"""The composer's sketch (ADR 0038 section 4, #1186) through the interpretation hand-off and the
worker side, against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider``: with the
flag on, a class with music is sketched (no songs, ``origin = "sketch"``, the class credit, its
music source left unset); a "No music" class keeps its soundless design; with the flag off
nothing changes. A chat change of a sketch is a sketch, with no credit, counted toward the
revise limit. Never the network, a real account or a model."""

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from classbeat.agent.records import Provenance
from classbeat.design import limits as design_limits
from classbeat.design import music_kind
from classbeat.design.interpretation_row import handoff_key
from designtools import FakeMowlMusic
from jobapi import owner_lock_key
from jobtools import engine_for
from sketchtools import NO_MUSIC, SketchEnv, composer_on
from test_interpret_handoff import wait_for_lock_waiters

pytestmark = pytest.mark.usefixtures("jenv")

SKETCH_MUSIC = {"source": "sketch"}


@pytest.mark.parametrize(
    ("flag", "music", "mode", "revision_music", "origin", "source"),
    [
        (True, True, "sketch", SKETCH_MUSIC, "sketch", None),
        (True, False, None, NO_MUSIC, "design_draft", NO_MUSIC),
        (False, True, None, None, "design_draft", "playlist"),
        (False, False, None, NO_MUSIC, "design_draft", NO_MUSIC),
    ],
)
async def test_the_handoff_with_the_flag_on_and_off_and_music_or_none(
    skenv: SketchEnv,
    fake_mowl: FakeMowlMusic,
    flag: bool,
    music: bool,
    mode: str | None,
    revision_music: Any,
    origin: str,
    source: Any,
) -> None:
    composer_on(skenv.renv, flag)
    class_id = await skenv.interpreted(music=music)
    before, mowl = skenv.ienv.credits(), len(fake_mowl.requests)
    made = await skenv.ienv.handoff(class_id)
    assert made.status_code == 201, made.text
    [job] = skenv.ienv.design_jobs(class_id)
    assert (job.kind, job.credit_state) == ("design_draft", "reserved")  # the class credit
    assert skenv.ienv.credits() == before + 1
    given = skenv.ienv.stored_input(job.id)
    assert given.get("mode") == mode
    if not flag and music:
        return  # today's design on the playlist (its run is covered by the design tests)
    skenv.renv.use("soundless_first_draft")
    await skenv.renv.run("design_draft")
    row = skenv.renv.denv.ops.row(job.id)
    assert (row.state, row.error_category) == ("completed", None)
    [only] = skenv.renv.revisions(class_id)
    assert skenv.renv.current(class_id) == only.id
    assert only.music == revision_music
    made_by = Provenance.model_validate_json(json.dumps(only.provenance))
    assert (made_by.origin, made_by.base_revision_id, made_by.request_message_id) == (
        origin, None, None,
    )  # fmt: skip
    assert skenv.music_source(class_id) == source
    assert len(fake_mowl.requests) == mowl  # no song, no MOWL call after the reading


async def test_a_sketch_class_is_never_read_as_soundless_or_studio(skenv: SketchEnv) -> None:
    class_id, _ = await skenv.sketched()
    async with skenv.renv.denv.ops.factory() as db:
        owner = skenv.renv.owner
        await db.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(owner)})
        assert await music_kind.refuse_soundless(db, owner, class_id) is None  # songs can fill it
        studio = await music_kind.admit_studio(db, owner, class_id)
        assert studio is not None
        assert studio.reason == "invalid_input"
        soundless = await music_kind.admit_music(db, owner, True, class_id)
        assert soundless is not None
        assert soundless.reason == "invalid_input"


async def test_a_sketch_class_reads_back_on_the_class_page_and_its_history(
    skenv: SketchEnv,
) -> None:
    class_id, sketch = await skenv.sketched()
    browser = skenv.renv.browser
    for path in (
        f"/api/classes/{class_id}",
        f"/api/classes/{class_id}/history",
        f"/api/classes/{class_id}/revisions",
        f"/api/classes/{class_id}/revisions/{sketch}",
        f"/api/classes/{class_id}/messages",
    ):
        got = await browser.get(path)
        assert got.status_code == 200, (path, got.text)
    detail = (await browser.get(f"/api/classes/{class_id}")).json()
    assert detail["current_revision_id"] == str(sketch)


async def test_two_handoffs_at_once_admit_one_sketch_and_one_credit(
    skenv: SketchEnv, database_url: str
) -> None:
    class_id = await skenv.interpreted()
    before = skenv.ienv.credits()
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(skenv.renv.owner)},
        )
        calls = [asyncio.create_task(skenv.ienv.handoff(class_id)) for _ in range(2)]
        await wait_for_lock_waiters(skenv.ienv, 2)
        holder.commit()
        answers = await asyncio.gather(*calls)
    assert sorted(a.status_code for a in answers) == [200, 201]
    assert answers[0].json() == answers[1].json()
    [job] = skenv.ienv.design_jobs(class_id)
    interpret_job = skenv.sql(
        "SELECT id FROM jobs WHERE class_id = :c AND kind = 'interpret_request'", c=class_id
    ).scalar_one()
    assert job.idempotency_key == handoff_key(interpret_job)
    assert skenv.ienv.stored_input(job.id)["mode"] == "sketch"
    assert skenv.ienv.credits() == before + 1


async def test_a_running_composer_class_keeps_its_design_on_a_mowl_playlist(
    skenv: SketchEnv,
) -> None:
    """The sketch's stage is the soundless one, cycling only: a running class is not sketched."""
    from classbeat.classes.interpretation_design import design_input
    from classbeat.design.interpretation_row import StoredInterpretation

    row = StoredInterpretation(
        job_id=UUID(int=1),
        brief={"sport": "running", "length_min": 30, "text": "a run"},
        music={"source": "mowl_spotify_playlist", "spotify_playlist_id": "0123456789abcdefABCDEF"},
        items=[],
    )
    given = {
        "request": "a 30 minute run",
        "mowl_connection_id": str(UUID(int=2)),
        "composer": True,
    }
    planned = design_input(row, given, UUID(int=3), song_picking=False, composer=True)
    assert not isinstance(planned, str)
    assert planned[1].model_dump().get("mode") is None


# --- a chat change of a sketch -----------------------------------------------------------------


async def test_a_revise_of_a_sketch_is_a_sketch_with_no_credit(
    skenv: SketchEnv, fake_mowl: FakeMowlMusic
) -> None:
    class_id, sketch = await skenv.sketched()
    credits, mowl = skenv.ienv.credits(), len(fake_mowl.requests)
    job_id, _ = await skenv.renv.revise(class_id, sketch, "soundless_revise_good", "Easier start")
    row = skenv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category, row.credit_state) == ("completed", None, "none")
    assert skenv.ienv.credits() == credits
    new = skenv.renv.current(class_id)
    assert new is not None
    assert new != sketch
    revised = skenv.revision(new)
    assert (revised.parent_id, revised.music) == (sketch, SKETCH_MUSIC)
    made_by = Provenance.model_validate_json(json.dumps(revised.provenance))
    assert made_by.origin == "sketch"
    assert made_by.base_revision_id == sketch
    assert made_by.request_message_id is not None
    assert skenv.music_source(class_id) is None  # still no music source
    history = (await skenv.renv.browser.get(f"/api/classes/{class_id}/history")).json()
    requests = {e["number"]: e["request"] for e in history["revisions"]}
    assert requests == {1: None, 2: "Easier start"}
    assert history["can_change"] is True  # the revise admits it, so the history says so
    assert len(fake_mowl.requests) == mowl


async def test_a_sketch_revise_counts_toward_the_revise_limit_and_the_first_sketch_does_not(
    skenv: SketchEnv,
) -> None:
    settings = skenv.renv.denv.app.settings
    design_limits.configure(settings.model_copy(update={"revisions_per_class": 1}))
    try:
        class_id, sketch = await skenv.sketched()
        await skenv.renv.revise(class_id, sketch, "soundless_revise_good", "Easier start")
        second = skenv.renv.current(class_id)
        assert second is not None
        refused = await skenv.renv.ask(class_id, second, "And shorter")
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert (detail["reason"], detail["count"], detail["limit"]) == ("revision_limit", 1, 1)
    finally:
        design_limits.configure(settings)


async def test_a_sketch_revise_is_behind_the_plans_coverage_and_not_the_switch(
    skenv: SketchEnv,
) -> None:
    class_id, sketch = await skenv.sketched()
    music_kind.configure(None)  # no plan covered (and the soundless switch off)
    refused = await skenv.renv.ask(class_id, sketch, "Easier start")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "soundless_not_evaluated"  # #1484
    assert len(skenv.renv.messages(class_id)) == 1  # only the request; nothing was written
