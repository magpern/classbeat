"""#1342 part 1 (ADR 0036 amendment A8, A10): a studio draft whose mix repeats a song, on the fakes
(the SwedeBeat gateway, ``FakeProvider``, the render child stood in); never the network.

- the draft is stored with the repeat, under a hash that covers it, and its timeline marks it;
- the SwedeBeat playlist is refused for it **at choice time** (``playlist_has_repeats``, no job, no
  gateway call) and **at job time** (the job ends ``error_category = 'playlist_has_repeats'``, not
  ``internal``, nothing written, not retried); a draft without repeats keeps the playlist;
- the mix is produced: one render entry per piece, each distinct track downloaded once and its
  sha256 checked once, X-Tracks one entry per piece (no loop runs in part 1) adding up to the mix.
"""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import revision
from classbeat.jobs.errors import RETRYABLE
from classbeat.studio import output_body_policy as body
from classbeat.swedebeat_studio import outputs_admit
from gatewaycore import MERGE_PATH, SESSION_PATH
from songtools import new_key
from studiooutputs import (
    Renders,
    choice,
    outputs_on,  # noqa: F401 - fixture
    produce,
    renders_fixture,  # noqa: F401 - the ``renders`` fixture
    rows,
    run_job,
    sent,
)
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    LENGTH,
    MIX_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    approved,
    design_script,
    run_kind,
    start_design,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")

# The eight approved tracks as MIX_8 plays them, then track 1 again for 90 s (not next to itself).
MIX_REPEAT: dict[str, Any] = {
    "tracks": [*MIX_8["tracks"], {"track": 1, "start_sec": 0, "end_sec": 90.0}],
    "crossfades": [8] * 8,
}


async def designed_with_repeat(studio: StudioEnv) -> tuple[Any, UUID, Any]:
    rider, class_id, list_id = await approved(studio)
    studio.songs.script = design_script(MIX_REPEAT)
    response = await start_design(rider, class_id, list_id)
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    current = studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).one()
    return rider, class_id, current


def track_downloads(studio: StudioEnv) -> int:
    return len([r for r in studio.gateway.requests if r.path.startswith("/tracks/")])


def asked_since(studio: StudioEnv, before: int) -> list[str]:
    """What the gateway was asked after ``before`` requests, but the route's session check."""
    return [r.path for r in studio.gateway.requests[before:] if r.path != SESSION_PATH]


async def test_the_repeat_is_stored_under_its_hash_and_marked_on_the_timeline(
    studio: StudioEnv,
) -> None:
    rider, class_id, current = await designed_with_repeat(studio)
    stored = studio.songs.revision(current.current_revision_id)
    spec = stored.music["mix_spec"]
    references = [t["reference"] for t in spec["tracks"]]
    assert len(references) == 9
    assert references[8] == references[0]
    assert spec["tracks"][8]["end_sec"] < LENGTH
    # A10: the repeats are in the hash; another repeat is another hash.
    assert bytes(stored.content_hash) == revision.studio_hash(stored.content, spec)
    moved = {**spec, "tracks": [*spec["tracks"][:8], {**spec["tracks"][8], "start_sec": 1.0}]}
    assert revision.studio_hash(stored.content, moved) != bytes(stored.content_hash)
    dropped = {**spec, "tracks": spec["tracks"][:8], "crossfades": spec["crossfades"][:7]}
    assert revision.studio_hash(stored.content, dropped) != bytes(stored.content_hash)
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert answer.status_code == 200, answer.text
    shown = answer.json()
    assert shown["has_repeats"] is True
    assert [t["repeat_of"] for t in shown["tracks"]] == [None] * 8 + [0]
    assert not any(t["loop"] for t in shown["tracks"])
    assert not any(r in answer.text for r in references)


async def test_the_playlist_is_refused_at_choice_time_and_the_mix_is_not(
    studio: StudioEnv,
) -> None:
    rider, class_id, current = await designed_with_repeat(studio)
    path = f"/api/classes/{class_id}/outputs"
    before = len(studio.gateway.requests)
    for chosen in (["swedebeat_playlist"], ["swedebeat_playlist", "swedebeat_mix"]):
        answer = await rider.browser.post(
            path, choice(current, studio, *chosen), {"Idempotency-Key": new_key()}
        )
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"]["reason"] == "playlist_has_repeats"
    assert (
        studio.songs.sql("SELECT count(*) FROM jobs WHERE kind = 'produce_outputs'").scalar() == 0
    )
    assert asked_since(studio, before) == []
    accepted = await rider.browser.post(
        path, choice(current, studio, "swedebeat_mix"), {"Idempotency-Key": new_key()}
    )
    assert accepted.status_code == 201, accepted.text


async def test_the_playlist_is_refused_again_when_the_job_runs(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, current = await designed_with_repeat(studio)
    before = len(studio.gateway.requests)
    # The admission's own check is bypassed here, so the job-time check is the one tested.
    monkeypatch.setattr(outputs_admit, "has_repeats", lambda _: False)
    answer = await rider.browser.post(
        f"/api/classes/{class_id}/outputs",
        choice(current, studio, "swedebeat_playlist"),
        {"Idempotency-Key": new_key()},
    )
    assert answer.status_code == 201, answer.text
    monkeypatch.undo()
    done = await run_job(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "playlist_has_repeats")  # no retry
    assert "playlist_has_repeats" not in RETRYABLE
    assert asked_since(studio, before) == []  # no bearer, no download, no write
    assert rows(studio, rider.user_id) == []  # no attempt opened, nothing written


async def test_the_mix_downloads_each_track_once_and_renders_every_piece(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, current = await designed_with_repeat(studio)
    before = track_downloads(studio)
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("completed", None)
    assert track_downloads(studio) - before == 8  # eight distinct tracks, not nine pieces
    (plan,) = renders.plans
    assert len(plan) == 9  # one render entry per piece
    assert plan[8]["path"] == plan[0]["path"]  # the repeat plays the same downloaded file
    (merge,) = sent(studio.gateway, "POST", MERGE_PATH)
    x_tracks = json.loads(merge.headers["x-tracks"])
    assert len(x_tracks) == 9  # a non-adjacent repeat is its own entry
    assert x_tracks[8]["title"] == x_tracks[0]["title"]
    stored = {r.output: r for r in rows(studio, rider.user_id)}["swedebeat_mix"]
    total = sum(item["duration"] for item in x_tracks)
    assert total == pytest.approx(stored.detail["duration_sec"], abs=0.01)
    assert body.played_seconds(stored.detail["plan"])[8] == pytest.approx(90.0, abs=1.0)
