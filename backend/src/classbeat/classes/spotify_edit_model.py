"""A Spotify draft as the edit reads it (ADR 0042, #1424). Pure: a record, no I/O."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from classbeat.design.edit_overlay_model import EditOverlay
from classbeat.design.revision_music_model import SpotifyRevisionMusic


@dataclass(frozen=True)
class SpotifyDraft:
    revision_id: UUID
    content_hash: bytes
    content: dict[str, Any]
    music: SpotifyRevisionMusic
    overlay: EditOverlay  # the stored ``edit_overlay`` (no record: NULL)
    edit_map: dict[str, Any]  # the map the draft was fitted to (``classes.spotify_edit_draft``)
