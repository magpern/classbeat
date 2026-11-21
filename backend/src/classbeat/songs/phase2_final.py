"""The final fenced transaction of a song pick's phase 2 (``classbeat.songs.phase2``, its module
docstring, step 7), for both kinds and the composer: the checks made again under ``hold`` before
anything is stored, the class's ``music_source`` and the MOWL id of the final playlist.

Split out of ``phase2`` (#1303, the code-structure rule "split on touch"); unchanged.
"""

from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from sqlalchemy import text

from classbeat.connections import hold
from classbeat.design.music import MusicStep
from classbeat.jobs import FencedSession, JobFailure
from classbeat.playlists import ClassPlaylist
from classbeat.playlists import common as playlist_common

if TYPE_CHECKING:
    from classbeat.songs.phase2 import Prepared

MUSIC_SOURCE: Final = "mowl_spotify_playlist"

_NEWEST = text(
    "SELECT id FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'song_list' ORDER BY number DESC LIMIT 1"
)
_LIVE_VERIFIED = text(
    "SELECT 1 FROM class_playlists WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :id AND playlist_id = :playlist_id AND revision_id = :revision_id "
    "AND state = 'verified' AND content_hash = :content_hash AND detached_at IS NULL"
)
_MOWL_ID = text(
    "UPDATE class_playlists SET mowl_playlist_id = :mowl_playlist_id, updated_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND detached_at IS NULL "
    "RETURNING id"
)


async def newest(fs: FencedSession, class_id: UUID) -> UUID | None:
    """The class's newest ``song_list`` revision."""
    params = {"owner_id": fs.lease.owner_id, "class_id": class_id}
    return (await fs.db.execute(_NEWEST, params)).scalar_one_or_none()


def tracks_hash(p: "Prepared") -> bytes:
    """``class_playlists.content_hash`` of a playlist holding exactly the approved ids in order."""
    return playlist_common.ids_hash(p.stored.track_ids)


async def final_checks(
    fs: FencedSession, p: "Prepared", final: ClassPlaylist, step: MusicStep
) -> None:
    """The final transaction's checks (step 7): both connections held again, the MOWL link the
    one analysed, the list still the newest, the class playlist still live and verified."""
    if not await hold(fs.db, fs.lease.ctx, p.spotify):
        raise JobFailure("not_connected")
    await step.still_current(fs)
    if await newest(fs, p.class_id) != p.revision_id:
        raise JobFailure("invalid_input")
    live = await fs.db.execute(
        _LIVE_VERIFIED,
        {
            "owner_id": fs.lease.owner_id,
            "class_id": p.class_id,
            "id": final.id,
            "playlist_id": final.playlist_id,
            "revision_id": p.revision_id,
            "content_hash": tracks_hash(p),
        },
    )
    if live.first() is None:
        raise JobFailure("not_connected")  # Disconnect detached it meanwhile


async def mark_mowl(
    fs: FencedSession, p: "Prepared", final: ClassPlaylist, mowl_playlist_id: int
) -> None:
    """``class_playlists.mowl_playlist_id`` of the final playlist (step 7)."""
    params = {
        "owner_id": fs.lease.owner_id,
        "class_id": p.class_id,
        "id": final.id,
        "mowl_playlist_id": mowl_playlist_id,
    }
    if (await fs.db.execute(_MOWL_ID, params)).first() is None:
        raise JobFailure("internal")


def music_source(final: ClassPlaylist) -> dict[str, Any]:
    """``classes.music_source`` of a class designed on picked songs (``ClassMusicSource``)."""
    return {
        "source": MUSIC_SOURCE,
        "spotify_playlist_id": final.playlist_id,
        "origin": "song_pick",
        "class_playlist_id": str(final.id),
    }
