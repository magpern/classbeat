"""The studio's Spotify writes never reach MOWL (#1535 issue section 4, #1546 item 4): MOWL's
wrapper is a snapshot of a Spotify playlist at attach time, and the studio rewrites the pool (its
working copy, ``swedebeat_studio.kind``) and fills a final playlist (``outputs_spotify``, then
"Sync again" through ``sync_kind``) without ever attaching or reading MOWL, so no stale wrapper
can be read; the next song pick attaches its own write anyway.

A behaviour test: the rider here **has** a MOWL connection (``studiotools`` riders have none), and
the fake MOWL of ``songtools`` sees no request through a studio pick, its design, the Spotify
output and a Sync again. On the fakes, never the network.
"""

from typing import Any
from uuid import UUID

import pytest

from classbeat import connections
from ctx import make_context
from optools import TOKEN_A
from studiooutputs import (
    outputs_on,  # noqa: F401 - fixture
    produce,
)
from studiotools import StudioEnv, StudioRider
from test_spotify_sync import run_sync
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    WHOLE_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default", "outputs_on")
ONE = r"/v1/playlists/[0-9A-Za-z]{22}"


@pytest.fixture
def with_mowl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every studio rider also connects MOWL (a fake token) before anything runs."""
    real = StudioEnv.rider

    async def rider(self: StudioEnv) -> StudioRider:
        made = await real(self)
        ctx = make_context(made.user_id)
        await connections.save(self.songs.factory, self.songs.ring, ctx, "mowl", {"token": TOKEN_A})
        return made

    monkeypatch.setattr(StudioEnv, "rider", rider)


@pytest.mark.usefixtures("with_mowl")
async def test_the_studio_spotify_writes_never_reach_mowl(studio: StudioEnv) -> None:
    songs = studio.songs
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    assert songs.pool(rider.user_id).playlist_id is not None  # the working copy was written
    providers = {r.provider for r in _connections(studio, rider.user_id)}
    assert "mowl" in providers
    # The final fill fails once (its owner check), so "Sync again" fills it through sync_kind.
    songs.fake.inject("status", method="GET", path=ONE, status=403, times=1)
    done = await produce(studio, rider, class_id, current, "spotify_playlist")
    assert (done.state, done.error_category) == ("failed", "forbidden")
    path = f"/api/classes/{class_id}/spotify-sync/{current.current_revision_id}"
    answer = await rider.browser.post(f"{path}/final")
    assert answer.status_code == 201, answer.text
    ran = await run_sync(studio, UUID(answer.json()["job_id"]))
    assert (ran.state, ran.error_category) == ("completed", None)
    assert songs.mowl.requests == []
    assert songs.mowl.attaches == []
    assert songs.operations(rider.user_id, "mowl_attach_spotify_playlist") == []


def _connections(studio: StudioEnv, owner: UUID) -> list[Any]:
    return studio.songs.sql("SELECT provider FROM connections WHERE owner_id = :o", o=owner).all()
