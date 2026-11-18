"""The composer e2e's fill and swap scripts on the e2e's own world (ADR 0038, #1191).

``e2e/tests/composer-workspace.spec.ts`` replays ``composer_fill`` and ``composer_swap`` against
the e2e stack's fake Spotify: ``spotifycore``'s default invented catalogue with every track at 240 s
(``e2e/fake-spotify``), one attached playlist holding one track and one pinned song (the reading
``composer_read`` stores). Here the scripts' own files run through the real fill, swap, approval and
phase 2 on that world, so a refs mismatch fails in this suite and not in a browser. The sketch is
the one the e2e's two changes leave (its song slots are the fill scripts' eight). No network, no
model.
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import spotifycore
from composertools import ComposerEnv, swap
from songtools import Rider, SongEnv
from spotifyfake import USER_A
from test_composer_phase2 import approve

SCRIPTS = Path(__file__).parent / "agent_scripts"

pytestmark = pytest.mark.usefixtures("jenv")

# The e2e's pick (``composer_fill``): "the invented band" finds catalogue tracks 0, 6, 12 ... and
# "placeholder collective" tracks 1, 7, 13 ...
PINNED = ("Signal Harbor 18", "The Invented Band")  # track 18, found by the first search
FAVOURED = 31  # the attached playlist's one track, found by the second search
SWAPPED = 3  # the song row the e2e swaps (0-based; slot 2 is the pin's)
PLAYLIST = "E2E Attached Climb"


def load(name: str) -> dict[str, Any]:
    return json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))


def title(songenv: SongEnv, track: int) -> str:
    return str(songenv.fake.catalogue[spotifycore.track_id(track)]["name"])


async def filled(cenv: ComposerEnv) -> tuple[Rider, UUID, UUID, UUID, UUID, str]:
    """The e2e's class up to its waiting list (rider, class, sketch, job, list, hash)."""
    fake = cenv.songenv.fake
    fake.catalogue = spotifycore._catalogue()  # pyright: ignore[reportPrivateUsage]
    fake.set_durations(240_000)  # as the e2e's fake sets them
    mine = fake.add_playlist(USER_A, PLAYLIST, [f"spotify:track:{spotifycore.track_id(FAVOURED)}"])
    rider = await cenv.songenv.rider()
    pinned = [{"title": PINNED[0], "artist": PINNED[1], "at": "the climb"}]
    class_id, sketch = await cenv.sketched(rider, sources=[mine.id], pinned=pinned)
    job_id, list_id, digest = await cenv.waiting(rider, class_id, load("composer_fill"))
    return rider, class_id, sketch, job_id, list_id, digest


def songs_of(cenv: ComposerEnv, list_id: UUID) -> list[tuple[str, dict[str, Any]]]:
    content = cenv.content(list_id)
    catalogue = cenv.songenv.fake.catalogue
    return [(catalogue[s["spotify_id"]]["name"], s["source"]) for s in content["songs"]]


async def test_the_fill_places_the_pin_and_the_attached_playlists_song(cenv: ComposerEnv) -> None:
    _, class_id, sketch, _, list_id, _ = await filled(cenv)
    songs = songs_of(cenv, list_id)
    assert len(songs) == 8
    assert songs[2] == (PINNED[0], {"kind": "pinned"})  # "the climb": the third slot
    assert (title(cenv.songenv, FAVOURED), {"kind": "playlist", "playlist": 0}) in songs
    assert [s for s in songs if s[1]["kind"] == "search"]  # the rest are classbeat's finds
    assert cenv.current(class_id) == sketch  # nothing replaced the sketch yet
    assert cenv.content(list_id).get("unmet", []) == []


async def test_the_swap_replaces_the_row_the_e2e_clicks_and_no_other(cenv: ComposerEnv) -> None:
    rider, class_id, _, _, list_id, _ = await filled(cenv)
    before = songs_of(cenv, list_id)
    made = await swap(rider, class_id, list_id, SWAPPED)
    assert made.status_code == 201, made.text
    cenv.songenv.script = load("composer_swap")
    row = await cenv.songenv.run(UUID(made.json()["job_id"]))
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    after = songs_of(cenv, row.approval_revision_id)
    assert [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b] == [SWAPPED]
    assert after[2] == (PINNED[0], {"kind": "pinned"})


async def test_the_approval_of_the_swapped_list_keeps_the_preview_and_asks_for_the_save(
    cenv: ComposerEnv,
) -> None:
    rider, class_id, sketch, _, list_id, _ = await filled(cenv)
    made = await swap(rider, class_id, list_id, SWAPPED)
    cenv.songenv.script = load("composer_swap")
    row = await cenv.songenv.run(UUID(made.json()["job_id"]))
    owner = rider.user_id
    assert cenv.songenv.class_playlists(owner) == []  # nothing final before the approval
    answer = await approve(rider, row.id, row.approval_revision_id, bytes(row.approval_hash).hex())
    assert answer.status_code == 200, answer.text
    done = await cenv.songenv.run(row.id)
    assert (done.state, done.error_category) == ("completed", None)
    draft = cenv.current(class_id)
    assert draft not in (None, sketch)
    assert cenv.songenv.revision(draft).provenance["fit"] == "kept"
    [playlist] = cenv.songenv.class_playlists(owner)
    assert playlist.state == "verified"
    view = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert view["save_pending"]["revision_id"] == str(draft)
    assert (view["save_pending"]["job_id"], view["phase2"]) == (str(row.id), None)
