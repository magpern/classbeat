"""SR C8 (ADR 0036 section 9): the approved draft's final Spotify playlist as a revision's sync, on
the fakes, against Postgres as ``classbeat_app``; never the network.

- the fill is the draft's ``final`` row: ``synced`` after the read-back, under its own lock key;
- a failed fill is the row's state, ``failed`` with its category, and the Spotify output's (C7):
  the SwedeBeat playlist is still produced; "Sync again" fills the **same** playlist (no second
  create) in the mix's order under the output's own fill key, so the output is then ``done``;
- **the race on one final playlist**: a newer revision's fill waits for the playlist's lock while
  an older one is in flight (it is not turned away busy), and the playlist ends with the newer
  content; an older fill that has not written yet when a newer revision is intended hands over
  without writing (``superseded``).
"""

import asyncio
import copy
import json
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from pickfake import POOL_14
from spotifycore import API_HOST
from studiooutputs import (
    outputs_on,  # noqa: F401 - fixture
    produce,
    rows,
    view,
)
from studiotools import StudioEnv, choose_turns, pool_turns, script
from test_spotify_sync import ITEMS, LIST_A, LIST_B, gate_first_put, run_sync, uris
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    MIX_8,
    WHOLE_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    design_script,
    designed,
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)
from test_swedebeat_studio_songs import SWAP_12, SWAPPED_LIST

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default", "outputs_on")
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
    rider, class_id, list_id, current = await designed(studio, WHOLE_8)
    before = set(studio.songs.fake.playlists)
    # The fill's owner check (the first read after the public create, roadmap Amendment 30) is
    # refused: nothing is written to the new playlist's items.
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


def test_the_e2e_studio_design_is_this_suites_design() -> None:
    """e2e/tests/studio.spec.ts replays the same design on the same maps (seeded there)."""
    path = Path(__file__).parent / "agent_scripts" / "e2e_studio_design.json"
    assert json.loads(path.read_text(encoding="utf-8")) == design_script(MIX_8)


def test_the_e2e_studio_swap_is_this_suites_swap() -> None:
    """The swap pick of e2e/tests/studio.spec.ts: the backend's ``_swap_script`` with the e2e
    catalogue's searches (two artists for the pool, two others for the swap round)."""
    path = Path(__file__).parent / "agent_scripts" / "e2e_studio_swap.json"
    expected = script(
        pool_turns(("the invented band", "placeholder collective"), POOL_14),
        pool_turns(("test pattern", "nobody real"), SWAP_12),
        choose_turns(SWAPPED_LIST),
    )
    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_the_e2e_studio_revise_is_this_suites_cut() -> None:
    """The edit loop of e2e/tests/studio.spec.ts: the last song cut at 230 s, with its note."""
    path = Path(__file__).parent / "agent_scripts" / "e2e_studio_revise.json"
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0
    assert json.loads(path.read_text(encoding="utf-8")) == design_script(
        cut, note="Cut the last song."
    )


# --- the race on one final playlist --------------------------------------------------------------


async def two_final_rows(studio: StudioEnv) -> tuple[Any, Any, Any, Any, Any]:
    """A class with its final playlist made, and two failed ``final`` rows of two revisions for
    that same playlist, each with its own fill key: the rider, the class, the playlist, and the
    older and the newer revision."""
    rider, class_id, _, current = await designed(studio)
    done = await produce(studio, rider, class_id, current, "spotify_playlist")
    assert done.state == "completed"
    (playlist,) = studio.songs.class_playlists(rider.user_id)
    revisions = []
    for ids in (LIST_A, LIST_B):
        revision_id = uuid.uuid4()
        studio.songs.sql(
            "INSERT INTO spotify_syncs (owner_id, class_id, revision_id, target, "
            "class_playlist_id, fill_key, lock_key, track_ids, state, detail) VALUES (:o, :c, :r, "
            "'final', :p, :k, :l, CAST(:t AS jsonb), 'failed', 'forbidden')",
            o=rider.user_id,
            c=class_id,
            r=revision_id,
            p=playlist.id,
            k=f"{revision_id}:final-fill",
            l=f"final:{playlist.id}",
            t=json.dumps(ids),
        )
        revisions.append(revision_id)
    return rider, class_id, playlist, revisions[0], revisions[1]


async def final_again(rider: Any, class_id: Any, revision_id: Any) -> UUID:
    answer = await rider.browser.post(f"/api/classes/{class_id}/spotify-sync/{revision_id}/final")
    assert answer.status_code == 201, answer.text
    return UUID(answer.json()["job_id"])


def final_state(studio: StudioEnv, owner: Any, revision_id: Any) -> tuple[str, str | None]:
    row = studio.songs.sql(
        "SELECT state, detail FROM spotify_syncs WHERE owner_id = :o AND revision_id = :r",
        o=owner,
        r=revision_id,
    ).one()
    return row.state, row.detail


def final_puts(studio: StudioEnv) -> list[Any]:
    return [s for s in studio.songs.fake.requests(API_HOST, ITEMS) if s.method == "PUT"]


async def test_a_newer_fill_waits_for_the_final_playlists_lock(studio: StudioEnv) -> None:
    rider, class_id, playlist, older, newer = await two_final_rows(studio)
    fake = studio.songs.fake
    entered, gate, original = gate_first_put(studio)
    older_run = asyncio.ensure_future(run_sync(studio, await final_again(rider, class_id, older)))
    assert await asyncio.to_thread(entered.wait, 20), "the older PUT never arrived"
    newer_run = asyncio.ensure_future(run_sync(studio, await final_again(rider, class_id, newer)))
    seen = len(fake.requests(API_HOST))
    await asyncio.sleep(1.5)
    assert len(fake.requests(API_HOST)) == seen, "a second write started while one was in flight"
    assert not newer_run.done()  # it waits for the lock, not turned away busy
    gate.set()
    older_row, newer_row = await asyncio.gather(older_run, newer_run)
    fake.handle = original  # type: ignore[method-assign]
    # Both ran in their one attempt: the newer one waited, it was not requeued busy.
    assert (older_row.state, newer_row.state) == ("completed", "completed")
    assert final_state(studio, rider.user_id, older) == ("synced", None)
    assert final_state(studio, rider.user_id, newer) == ("synced", None)
    assert fake.playlists[playlist.playlist_id].entries == uris(LIST_B)


async def test_a_stale_fill_hands_over_without_writing(studio: StudioEnv) -> None:
    rider, class_id, playlist, older, newer = await two_final_rows(studio)
    older_job = await final_again(rider, class_id, older)
    newer_job = await final_again(rider, class_id, newer)  # intended after the older one
    count = len(final_puts(studio))
    done = await run_sync(studio, older_job)
    assert (done.state, done.error_category) == ("completed", None)
    assert final_state(studio, rider.user_id, older) == ("failed", "superseded")
    assert len(final_puts(studio)) == count  # nothing written
    assert (await run_sync(studio, newer_job)).state == "completed"
    assert final_state(studio, rider.user_id, newer) == ("synced", None)
    assert studio.songs.fake.playlists[playlist.playlist_id].entries == uris(LIST_B)
