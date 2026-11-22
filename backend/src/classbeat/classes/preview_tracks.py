"""The Spotify tracks of a class's current draft, for the listening preview (ADR 0042 section 7.2).
Read only, owner-scoped.

``load`` reads the user's class (``owner_id`` and ``class_id`` together, under row security too)
with its current revision and returns its ``Track`` list in play order (the 0-based ``piece``
index of section 2), else a reason:

- ``not_found``: no such class of the user (another user's class answers the same);
- ``no_draft``: no current revision, a current revision that is no ``draft`` whose ``music`` is a
  Spotify playlist's (``SpotifyRevisionMusic``), or a draft that names no approved ``song_list``
  (a class designed directly on a MOWL link before song lists, which records no Spotify id), or
  whose list no longer loads (``song_list.load`` re-checks its hash and its fingerprint).

The ids and lengths come only from that stored, hash-checked list, never from the browser; each
id is checked again (``player_policy.track_uri``) before it is sent.
"""

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.design.revision import SpotifyRevisionMusic
from classbeat.songs import song_list

Factory = async_sessionmaker[AsyncSession]

_CURRENT: Final = text(
    "SELECT c.current_revision_id AS current, r.kind, r.music FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.id = :class_id AND c.owner_id = :owner_id"
)


@dataclass(frozen=True, slots=True)
class Track:
    track_id: str
    length_ms: int


@dataclass(frozen=True, slots=True)
class DraftTracks:
    revision_id: UUID
    tracks: tuple[Track, ...]

    @property
    def track_ids(self) -> tuple[str, ...]:
        return tuple(t.track_id for t in self.tracks)


def _music(raw: object) -> SpotifyRevisionMusic | None:
    if not isinstance(raw, dict):
        return None
    try:
        return SpotifyRevisionMusic.model_validate_json(json.dumps(raw))
    except ValidationError:
        return None


async def load(factory: Factory, ctx: UserContext, class_id: UUID) -> DraftTracks | str:
    """The current draft's tracks, or ``not_found`` / ``no_draft`` (module docstring)."""
    async with user_session(factory, ctx) as db:
        params = {"class_id": class_id, "owner_id": ctx.user_id}
        row = (await db.execute(_CURRENT, params)).first()
        if row is None:
            return "not_found"
        music = _music(row.music) if row.kind == "draft" else None
        if row.current is None or music is None or music.song_list_id is None:
            return "no_draft"
        stored = await song_list.load(db, ctx.user_id, class_id, music.song_list_id)
    if stored is None:
        return "no_draft"
    tracks = tuple(Track(s.spotify_id, s.length_sec * 1000) for s in stored.content.songs)
    return DraftTracks(row.current, tracks)
