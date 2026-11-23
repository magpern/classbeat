"""CP-R2 (#1394, ADR 0042 sections 6.3 to 6.5): the audition's listening sessions, clips and
``preview_render`` job, on the fakes only (the SwedeBeat gateway's read-only download; the render
child stood in by ``studiooutputs.Renders``); never the network.

- a clip renders once and is then served to its owner only: another user's session or class, and
  another class's session, are 404 like an unknown one; the file is ``inline`` and
  ``private, no-store``, and a ``Range`` re-request costs nothing;
- past ``expires_at``, or once a new clip would take the session past 25 min, it is ``410
  preview_session_ended`` (and nothing is rendered for it);
- a workout-only edit is a cache hit; an audio edit is a miss;
- an edit and an undo of the class go on while a clip renders (the job is outside its job slot);
- a second window reuses the cached source tracks, while the output mix still downloads every track
  fresh (it never reads the cache);
- the job is never offered by the jobs API; sessions and renders are rate-limited per owner.
"""

import json
import os
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from classbeat.design import revision
from classbeat.design.revision import StudioRevisionMusic
from classbeat.preview import session_policy
from cliptools import (
    get_clip,
    new_session,
    rendered,
    run_preview,
    volume_fixture,  # noqa: F401 - the ``volume`` fixture
)
from studiooutputs import (
    Renders,
    outputs_on,  # noqa: F401 - fixture
    produce,
    renders_fixture,  # noqa: F401 - the ``renders`` fixture
)
from studiotools import StudioEnv, pick_script
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    downloads,
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default", "volume", "renders")


def delivered(studio: StudioEnv, session: str) -> int:
    found = studio.songs.sql("SELECT delivered_ms FROM preview_sessions WHERE id = :s", s=session)
    return int(found.scalar_one())


def new_current(studio: StudioEnv, class_id: UUID, *, content: Any = None, spec: Any = None) -> Any:
    """A new current draft of the class: its workout or its mix replaced, the hash recomputed."""
    current = studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    base = studio.songs.revision(current)
    music = dict(base.music)
    if spec is not None:
        music["mix_spec"] = spec
    content = base.content if content is None else content
    parsed = StudioRevisionMusic.model_validate_json(json.dumps(music))
    checked = parsed.mix_spec.model_dump(mode="json")
    new_id = studio.songs.sql(
        "INSERT INTO class_revisions (owner_id, class_id, number, kind, content, content_hash, "
        "music, provenance, parent_id) SELECT owner_id, class_id, "
        "(SELECT max(number) + 1 FROM class_revisions WHERE class_id = :c), kind, "
        "CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), provenance, id "
        "FROM class_revisions WHERE id = :base RETURNING id",
        c=class_id, base=base.id, content=json.dumps(content), music=json.dumps(music),
        hash=revision.studio_hash(content, checked),
    ).scalar_one()  # fmt: skip
    studio.songs.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c", r=new_id, c=class_id
    )
    return studio.songs.revision(new_id)


async def test_a_clip_renders_once_and_is_served_to_its_owner_only(
    studio: StudioEnv, renders: Renders, volume: Path
) -> None:
    alice, class_id, _, _ = await designed(studio)
    session = await new_session(alice, class_id)
    served = await rendered(studio, alice, class_id, session)
    assert len(renders.plans) == 1
    assert served.headers["content-type"] == "audio/mpeg"
    assert served.headers["content-disposition"] == "inline"
    assert served.headers["cache-control"] == "private, no-store"
    start, end = int(served.headers["x-preview-start-ms"]), int(served.headers["x-preview-end-ms"])
    assert start == 0 < end <= 360_000
    (stored,) = (volume / str(alice.user_id) / "clips").iterdir()
    assert served.content == stored.read_bytes()
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    assert delivered(studio, session) == end - start
    # A Range re-request (a seek) is served and counts nothing more.
    partial = await get_clip(alice, class_id, session, Range="bytes=0-9")
    assert (partial.status_code, len(partial.content)) == (206, 10)
    assert delivered(studio, session) == end - start
    assert len(renders.plans) == 1

    studio.songs.script = pick_script()  # the second user's pick
    bob, bobs_class, _, _ = await designed(studio)
    unknown = await get_clip(bob, bobs_class, "00000000-0000-4000-8000-000000000000")
    assert unknown.status_code == 404
    for who, cls in ((bob, class_id), (bob, bobs_class), (alice, bobs_class)):
        foreign = await get_clip(who, cls, session)
        assert foreign.status_code == 404, foreign.text
        assert foreign.json() == unknown.json()
    other = studio.songs.sql(
        "INSERT INTO classes (owner_id, title) VALUES (:o, 'Another') RETURNING id", o=alice.user_id
    ).scalar_one()
    assert (await get_clip(alice, other, session)).status_code == 404  # another class's session
    made = await bob.browser.post(f"/api/classes/{class_id}/preview/sessions", {})
    assert made.status_code == 404
    assert delivered(studio, session) == end - start


async def test_an_ended_session_is_410_and_nothing_is_rendered_for_it(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, _, _ = await designed(studio)
    session = await new_session(rider, class_id)
    served = await rendered(studio, rider, class_id, session)
    end = int(served.headers["x-preview-end-ms"])
    # Near the cap: the counted clip is still free, a new one is refused before any render.
    studio.songs.sql(
        "UPDATE preview_sessions SET delivered_ms = :ms WHERE id = :s",
        ms=session_policy.DELIVERY_CAP_MS - 1000, s=session,
    )  # fmt: skip
    assert (await get_clip(rider, class_id, session)).status_code == 200
    over = await get_clip(rider, class_id, session, end)
    assert over.status_code == 410, over.text
    assert over.json() == {"detail": {"reason": "preview_session_ended"}}
    assert studio.songs.sql("SELECT count(*) FROM jobs WHERE kind = 'preview_render'").scalar() == 1
    # Past its lifetime even a counted clip is refused.
    studio.songs.sql(
        "UPDATE preview_sessions SET created_at = now() - interval '31 minutes', "
        "expires_at = now() - interval '1 minute' WHERE id = :s", s=session,
    )  # fmt: skip
    gone = await get_clip(rider, class_id, session)
    assert gone.status_code == 410
    assert gone.json() == {"detail": {"reason": "preview_session_ended"}}
    assert len(renders.plans) == 1


async def test_a_start_that_is_not_in_the_mix_is_422_invalid_start(studio: StudioEnv) -> None:
    rider, class_id, _, _ = await designed(studio)
    session = await new_session(rider, class_id)
    beyond = 24 * 3600 * 1000  # the route's top; the mix is far shorter
    for start in (beyond, beyond + 1, cast(Any, "-1"), cast(Any, "x")):
        refused = await get_clip(rider, class_id, session, start)
        assert refused.status_code == 422, (start, refused.text)
        assert refused.json() == {"detail": {"reason": "invalid_start"}}
    assert studio.songs.sql("SELECT count(*) FROM jobs WHERE kind = 'preview_render'").scalar() == 0


async def test_a_workout_only_edit_is_a_hit_and_an_audio_edit_a_miss(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, _, current = await designed(studio)
    session = await new_session(rider, class_id)
    await rendered(studio, rider, class_id, session)
    base = studio.songs.revision(current.current_revision_id)
    new_current(studio, class_id, content={**base.content, "edited": True})
    hit = await get_clip(rider, class_id, session)
    assert hit.status_code == 200, hit.text
    assert len(renders.plans) == 1
    spec = json.loads(json.dumps(base.music["mix_spec"]))
    spec["crossfades"][0] = spec["crossfades"][0] - 2
    new_current(studio, class_id, spec=spec)
    miss = await get_clip(rider, class_id, session)
    assert miss.status_code == 202, miss.text
    row = await run_preview(studio, UUID(miss.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    assert len(renders.plans) == 2


async def test_an_edit_and_an_undo_go_on_while_a_clip_renders(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    session = await new_session(rider, class_id)
    asked = await get_clip(rider, class_id, session)
    assert asked.status_code == 202
    base = studio.songs.revision(current.current_revision_id)
    edited = new_current(studio, class_id, content={**base.content, "edited": True})
    body = {"revision_id": str(base.id), "content_hash": bytes(base.content_hash).hex()}
    undo = await rider.browser.post(f"/api/classes/{class_id}/current", body)
    assert undo.status_code == 200, undo.text  # not class_busy
    assert edited.id != base.id
    row = await run_preview(studio, UUID(asked.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    assert (await get_clip(rider, class_id, session)).status_code == 200


async def test_a_second_window_reuses_the_cached_tracks_and_the_output_mix_downloads_fresh(
    studio: StudioEnv,
    renders: Renders,
    outputs_on: None,  # noqa: F811 - the fixture
) -> None:
    rider, class_id, _, current = await designed(studio)
    session = await new_session(rider, class_id)
    before = len(downloads(studio))
    first = await rendered(studio, rider, class_id, session)
    references = {
        r["reference"]
        for r in studio.songs.revision(current.current_revision_id).music["mix_spec"]["tracks"]
    }
    assert len(downloads(studio)) - before == len(references)  # each distinct track, once
    cached = studio.songs.sql("SELECT count(*) FROM preview_sources").scalar()
    assert cached == len(references)
    await rendered(studio, rider, class_id, session, int(first.headers["x-preview-end-ms"]))
    assert len(downloads(studio)) - before == len(references)  # from the cache: no download
    # The output mix never reads the cache: each of its tracks is downloaded fresh.
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("completed", None)
    assert len(downloads(studio)) - before == 2 * len(references)


async def test_the_jobs_api_never_offers_a_preview_render(studio: StudioEnv) -> None:
    rider, class_id, _, _ = await designed(studio)
    body = {"kind": "preview_render", "input": {"preview_class_id": str(class_id), "start_ms": 0,
            "end_ms": 1000, "key": "a" * 64}}  # fmt: skip
    answer = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": os.urandom(8).hex()})
    assert answer.status_code in (400, 404, 422), answer.text
    assert "unknown_kind" in answer.text
    assert studio.songs.sql("SELECT count(*) FROM jobs WHERE kind = 'preview_render'").scalar() == 0


@pytest.mark.usefixtures("frozen_rate_windows")
async def test_sessions_and_renders_are_rate_limited_per_owner(studio: StudioEnv) -> None:
    rider, class_id, _, _ = await designed(studio)
    session = await new_session(rider, class_id)
    first = await get_clip(rider, class_id, session)
    again = await get_clip(rider, class_id, session)  # the same key: the same open job
    assert (first.status_code, again.status_code) == (202, 202)
    assert first.json()["job_id"] == again.json()["job_id"]
    second = await get_clip(rider, class_id, session, int(first.json()["end_ms"]))
    assert second.status_code == 202
    third = await get_clip(rider, class_id, session, int(second.json()["end_ms"]))
    assert third.status_code == 429, third.text  # OPEN_RENDERS open already
    studio.songs.sql(
        "INSERT INTO preview_sessions (owner_id, class_id, expires_at) "
        "SELECT :o, :c, now() + interval '1 minute' FROM generate_series(2, :n)",
        o=rider.user_id, c=class_id, n=session_policy.SESSIONS_PER_HOUR,
    )  # fmt: skip
    refused = await rider.browser.post(f"/api/classes/{class_id}/preview/sessions", {})
    assert refused.status_code == 429, refused.text
