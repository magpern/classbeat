"""Response models of the Music page's playlists (ADR 0013 section 4). Split out of
``classbeat.classes.schemas``; no Spotify id appears in them."""

from uuid import UUID

from pydantic import BaseModel


class MosaicSongOut(BaseModel):
    """One of the up to four songs of a playlist's mosaic: its title (for the initials tile) and
    its album cover on the pinned host, or null. No Spotify id."""

    title: str
    cover: str | None = None


class MusicPlaylistOut(BaseModel):
    """A playlist classbeat made in the owner's own Spotify, for the Music page. ``link`` is the
    owner's own, built by the API from the stored id of a verified playlist (ADR 0013 section 4)."""

    class_id: UUID
    class_title: str
    name: str
    link: str | None = None
    detached: bool = False
    note: str | None = None
    songs: list[MosaicSongOut]


class MusicPlaylistsOut(BaseModel):
    playlists: list[MusicPlaylistOut]
