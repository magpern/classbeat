"""SR C8 (ADR 0036 section 9): the approved draft's final Spotify playlist as a revision's sync, on
the fakes, against Postgres as ``classbeat_app``; never the network.

- the fill is the draft's ``final`` row: ``synced`` after the read-back, under its own lock key;
- a failed fill is the row's state, ``failed`` with its category, and the Spotify output's (C7):
  the SwedeBeat playlist is still produced; "Sync again" fills the **same** playlist (no second
  create) in the mix's order under the output's own fill key, so the output is then ``done``.
"""

from typing import Any
from uuid import UUID

import pytest

from studiooutputs import (
    outputs_on,  # noqa: F401 - fixture
    produce,
    rows,
    view,
)
from studiotools import StudioEnv
from test_spotify_sync import run_sync
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")
ONE = r"/v1/playlists/[0-9A-Za-z]{22}"


def mix_order(studio: StudioEnv, list_id: Any, draft_id: Any) -> list[str | None]:
    spec = studio.songs.revision(draft_id).music["mix_spec"]
    songs = {
        s["swedebeat"]["reference"]: s["spotify_id"]
        for s in studio.songs.revision(list_id).content["songs"]
    }
    return [f"spotify:track:{songs[t['reference']]}" for t in spec["tracks"]]


def final_row(studio: StudioEnv, owner: Any) -> Any:
    return studio.songs.sql(
        "SELECT * FROM spotify_syncs WHERE owner_id = :o AND target = 'final'", o=owner
    ).one()


async def test_the_final_fill_is_the_drafts_sync(studio: StudioEnv) -> None:
    rider, class_id, list_id, current = await designed(studio)
    before = set(studio.songs.fake.playlists)
    done = await produce(studio, rider, class_id, current, "spotify_playlist")
    assert (done.state, done.error_category) == ("completed", None)
    (made,) = [studio.songs.fake.playlists[p] for p in set(studio.songs.fake.playlists) - before]
    row = final_row(studio, rider.user_id)
    assert (row.revision_id, row.state, row.detail) == (current.current_revision_id, "synced", None)
    (playlist,) = studio.songs.class_playlists(rider.user_id)
    assert row.class_playlist_id == playlist.id
    assert row.lock_key == f"final:{playlist.id}"
    assert made.entries == mix_order(studio, list_id, current.current_revision_id)
    path = f"/api/classes/{class_id}/spotify-sync/{current.current_revision_id}"
    shown = await rider.browser.get(path)
    assert shown.json() == {"working_copy": None, "final": {"state": "synced", "detail": None}}


async def test_a_failed_fill_never_blocks_the_swedebeat_outputs_and_sync_again_fills_it(
    studio: StudioEnv,
) -> None:
    rider, class_id, list_id, current = await designed(studio)
    before = set(studio.songs.fake.playlists)
    # The fill's owner check is refused: nothing is written to the new playlist's items.
    studio.songs.fake.inject("status", method="GET", path=ONE, status=403, times=1)
    done = await produce(studio, rider, class_id, current, "spotify_playlist", "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "forbidden")
    assert {(r.output, r.state) for r in rows(studio, rider.user_id)} == {
        ("spotify_playlist", "failed"),
        ("swedebeat_playlist", "done"),
    }
    (made,) = [studio.songs.fake.playlists[p] for p in set(studio.songs.fake.playlists) - before]
    assert made.entries == []
    row = final_row(studio, rider.user_id)
    assert (row.state, row.detail) == ("failed", "forbidden")
    path = f"/api/classes/{class_id}/spotify-sync/{current.current_revision_id}"
    assert (await rider.browser.get(path)).json()["final"] == {
        "state": "failed",
        "detail": "forbidden",
    }

    answer = await rider.browser.post(f"{path}/final")
    assert answer.status_code == 201, answer.text
    sync_job = UUID(answer.json()["job_id"])
    ran = await run_sync(studio, sync_job)
    assert (ran.state, ran.error_category) == ("completed", None)
    assert (final_row(studio, rider.user_id).state, made.id) == ("synced", made.id)
    assert set(studio.songs.fake.playlists) - before == {made.id}  # no second create
    assert made.entries == mix_order(studio, list_id, current.current_revision_id)
    (playlist,) = studio.songs.class_playlists(rider.user_id)
    assert playlist.state == "verified"
    keys = [
        o.idempotency_key
        for o in studio.songs.operations(rider.user_id)
        if o.kind == "spotify_replace_items" and o.class_id == class_id
    ]
    assert len(keys) == 1  # the output's own fill, taken over: no second operation
    assert keys[0].endswith(":spotify_playlist:1:spotify_fill")
    assert (await view(rider, class_id))["spotify_playlist"]["state"] == "done"
