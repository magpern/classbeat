"""What the audition keeps and when it goes (#1394, ADR 0042 section 6.5), on the fakes: rows and
files after 14 idle days, over the per-owner size cap, with the class, with the account and with
the SwedeBeat connection, and the orphan sweep of files no row names."""

import os
import time
import uuid
from pathlib import Path

import pytest

from classbeat import connections
from classbeat.account import erase
from classbeat.auth.purge import purge_expired
from classbeat.preview import purge, storage
from cliptools import (
    new_session,
    rendered,
    volume_fixture,  # noqa: F401 - the ``volume`` fixture
)
from ctx import make_context
from studiooutputs import renders_fixture  # noqa: F401 - the ``renders`` fixture
from studiotools import StudioEnv, StudioRider, pick_script
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default", "volume", "renders")
OLD = time.time() - 2 * purge.ORPHAN_GRACE


def files(volume: Path, rider: StudioRider) -> list[Path]:
    owner = volume / str(rider.user_id)
    return sorted(
        p for kind in storage.KINDS if (owner / kind).is_dir() for p in (owner / kind).iterdir()
    )


def count(studio: StudioEnv, table: str, rider: StudioRider) -> int:
    found = studio.songs.sql(f"SELECT count(*) FROM {table} WHERE owner_id = :o", o=rider.user_id)  # noqa: S608
    return int(found.scalar_one())


async def with_a_clip(studio: StudioEnv) -> tuple[StudioRider, uuid.UUID]:
    studio.songs.script = pick_script()  # each user's own pick
    rider, class_id, _, _ = await designed(studio)
    await rendered(studio, rider, class_id, await new_session(rider, class_id))
    return rider, class_id


async def purged(studio: StudioEnv) -> dict[str, int]:
    return await purge_expired(studio.songs.factory, studio.songs.spot.env.settings)


async def test_idle_clips_and_sources_and_day_old_sessions_go_with_their_files(
    studio: StudioEnv, volume: Path
) -> None:
    rider, _ = await with_a_clip(studio)
    assert len(files(volume, rider)) == 1 + count(studio, "preview_sources", rider)
    await purged(studio)  # nothing is old yet
    assert (count(studio, "preview_clips", rider), count(studio, "preview_sessions", rider)) == (
        1,
        1,
    )
    for table in ("preview_clips", "preview_sources"):
        studio.songs.sql(
            f"UPDATE {table} SET last_used_at = now() - interval '15 days' WHERE owner_id = :o",  # noqa: S608
            o=rider.user_id,
        )
    studio.songs.sql(
        "UPDATE preview_sessions SET created_at = now() - interval '25 hours', "
        "expires_at = now() - interval '24 hours 30 minutes' WHERE owner_id = :o",
        o=rider.user_id,
    )
    done = await purged(studio)
    assert done["preview_clips"] >= 1
    assert done["preview_sessions"] >= 1
    for table in ("preview_clips", "preview_sources", "preview_sessions"):
        assert count(studio, table, rider) == 0
    assert files(volume, rider) == []


async def test_the_size_cap_keeps_the_most_recently_used(studio: StudioEnv, volume: Path) -> None:
    rider, _ = await with_a_clip(studio)
    (clip,) = (volume / str(rider.user_id) / "clips").iterdir()
    studio.songs.sql(
        "UPDATE preview_sources SET last_used_at = now() - interval '1 hour' WHERE owner_id = :o",
        o=rider.user_id,
    )
    storage.configure(volume, clip.stat().st_size)  # room for the newest file only
    await purged(studio)
    assert (count(studio, "preview_clips", rider), count(studio, "preview_sources", rider)) == (
        1,
        0,
    )
    assert files(volume, rider) == [clip]


async def test_the_files_go_with_the_class_and_with_the_account(
    studio: StudioEnv, volume: Path
) -> None:
    rider, class_id = await with_a_clip(studio)
    other, _ = await with_a_clip(studio)
    deleted = await rider.browser.client.request(
        "DELETE", f"/api/classes/{class_id}", headers=await rider.browser.headers()
    )
    rider.browser.client.cookies.clear()
    assert deleted.status_code == 200, deleted.text
    assert files(volume, rider) == []
    assert count(studio, "preview_clips", rider) == 0
    assert files(volume, other) != []  # another user's stay
    assert await erase.delete_row(studio.songs.factory, make_context(other.user_id))
    assert not (volume / str(other.user_id)).exists()


async def test_a_swedebeat_disconnect_forgets_the_cache_and_the_sweep_its_files(
    studio: StudioEnv, volume: Path
) -> None:
    rider, _ = await with_a_clip(studio)
    for path in files(volume, rider):
        os.utime(path, (OLD, OLD))
    ctx = make_context(rider.user_id)
    assert await connections.disconnect(studio.songs.factory, ctx, "swedebeat")
    assert (count(studio, "preview_clips", rider), count(studio, "preview_sources", rider)) == (
        0,
        0,
    )
    done = await purged(studio)
    assert done["preview_clips_orphans"] == 1
    assert done["preview_sources_orphans"] >= 1
    assert files(volume, rider) == []


async def test_the_sweep_takes_old_orphans_only_and_rows_whose_file_is_gone(
    studio: StudioEnv, volume: Path
) -> None:
    rider, _ = await with_a_clip(studio)
    clips = volume / str(rider.user_id) / "clips"
    (clip,) = clips.iterdir()
    fresh = clips / f"{uuid.uuid4()}.mp3"  # placed for an insert still in flight
    fresh.write_bytes(b"x")
    stale = clips / f"{uuid.uuid4()}.mp3"
    stale.write_bytes(b"x")
    temp = clips / ".tmp-0123456789abcdef"
    temp.write_bytes(b"x")
    stray = volume / str(uuid.uuid4())  # no user's directory
    stray.mkdir(mode=0o700)
    for path in (stale, temp, stray):
        os.utime(path, (OLD, OLD))
    await purged(studio)
    assert sorted(clips.iterdir()) == sorted([clip, fresh])
    assert not stray.exists()
    clip.unlink()
    await purged(studio)
    assert count(studio, "preview_clips", rider) == 0  # its file was gone: the row goes too
