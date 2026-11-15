"""The class document of one approved revision, from the database (ADR 0034 sections 4 and 5).

Everything comes from stored rows: the revision's ``content`` and ``music`` (after
``saving.approval.revision`` checked them and their hash), and the songs only from the approved
``song_list`` revision the draft names (``songs.song_list.load``, its own hash checked again).
A draft with music and no song list (a Swedebeat mix, a playlist the user linked) has no songs
the player can be given: ``player_format_policy`` refuses it (``no_songs``).
"""

import json
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.revision import NoRevisionMusic, SpotifyRevisionMusic
from classbeat.player_class_calc import PickedSong, player_class
from classbeat.player_format_model import MusicSource, Sport
from classbeat.player_format_policy import Converted, Refusal, convert
from classbeat.saving.approval import SAVED_MUSIC, Revision
from classbeat.songs import song_list

UNREADABLE = Refusal("unreadable", "The class could not be read.")
_ROW = text(
    "SELECT content, music FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :revision_id"
)


async def document_of(db: AsyncSession, owner_id: UUID, revision: Revision) -> Converted | Refusal:
    row = (
        await db.execute(
            _ROW,
            {
                "owner_id": owner_id,
                "class_id": revision.class_id,
                "revision_id": revision.revision_id,
            },
        )
    ).first()
    if row is None or not isinstance(row.content, dict):
        return UNREADABLE
    try:
        music = SAVED_MUSIC.validate_json(json.dumps(row.music))
    except ValidationError:
        return UNREADABLE
    draft = cast(dict[str, Any], row.content)
    sport: Sport = "running" if revision.running else "cycling"
    if isinstance(music, NoRevisionMusic):
        return convert(player_class(draft, sport, "none", 0, ()))
    source: MusicSource = "spotify" if isinstance(music, SpotifyRevisionMusic) else "swedebeat"
    songs: tuple[PickedSong, ...] = ()
    crossfade = 0.0
    list_id = music.song_list_id if isinstance(music, SpotifyRevisionMusic) else None
    if list_id is not None:
        picked = await song_list.load(db, owner_id, revision.class_id, list_id)
        if picked is None:
            return UNREADABLE
        crossfade = picked.content.crossfade_sec
        songs = tuple(
            PickedSong(s.title, s.artist, s.length_sec, s.bpm, s.spotify_id)
            for s in picked.content.songs
        )
    return convert(player_class(draft, sport, source, crossfade, songs))
