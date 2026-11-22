"""The class's current Spotify draft, read back and re-checked for a user edit (ADR 0042 section
4, #1424). The twin of ``swedebeat_studio.current_draft`` for a draft on the user's Spotify
playlist. Read only; the caller's transaction (the web request's user session).

``load`` reads the user's class (``owner_id`` and ``class_id`` together, under row security too)
with its ``current_revision_id`` and that revision, and returns a ``SpotifyDraft`` only when all of
this holds, otherwise a reason:

- ``not_found``: no such class of the user (another user's class answers the same);
- ``no_spotify_draft``: no current revision, a revision that is no ``draft``, or one whose
  ``music`` is not a Spotify playlist's (``SpotifyRevisionMusic``): a studio draft, a mix, a
  soundless class;
- ``internal``: the stored ``content_hash`` is no longer ``revision_hash`` of the content and its
  ``edit_overlay`` (the one hash: a revision is immutable, so this never happens without
  tampering), or its overlay does not read;
- ``edit_unavailable``: the draft has no edit map (a draft made before #1424, a running class, a
  map past ``revision_edit_map.MAX_BYTES``) or its nearest source of one is not this music. The
  page opens read-only, as it did for every Spotify draft before.

**Where the map is read.** ``music.edit_map`` of the draft itself, else, for a ``user_edit``
revision (which keeps none), of its nearest ancestor that is not a ``user_edit``
(``mix_proposal.not_user_edit``), which must be a draft on the same playlist fingerprint. Both
are rows of this user's class, stored by the worker when it fitted the draft: **no MOWL read, no
request**.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.edit_overlay_model import EditOverlay, parsed
from classbeat.design.revision import SpotifyRevisionMusic
from classbeat.design.revision_hash import revision_hash
from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.swedebeat_studio import mix_proposal

NO_SPOTIFY_DRAFT = "no_spotify_draft"
EDIT_UNAVAILABLE = "edit_unavailable"

_SELECT = text(
    "SELECT c.current_revision_id AS current, r.kind, r.content, r.content_hash, r.music, "
    "r.edit_overlay, r.provenance ->> 'origin' AS origin FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_MUSIC = text(
    "SELECT music FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :id AND kind = 'draft'"
)


@dataclass(frozen=True)
class SpotifyDraft:
    revision_id: UUID
    content_hash: bytes
    content: dict[str, Any]
    music: SpotifyRevisionMusic
    overlay: EditOverlay  # the stored ``edit_overlay`` (no record: NULL)
    edit_map: dict[str, Any]  # the map the draft was fitted to (module docstring)


def _music(raw: object) -> SpotifyRevisionMusic | None:
    if not isinstance(raw, dict):
        return None
    try:
        return SpotifyRevisionMusic.model_validate_json(json.dumps(raw))
    except ValidationError:
        return None


async def _ancestors_map(
    db: AsyncSession, owner_id: UUID, class_id: UUID, start: UUID, fingerprint: str
) -> dict[str, Any] | None:
    """The edit map of the nearest ancestor of ``start`` that is not a ``user_edit``."""
    at = await mix_proposal.not_user_edit(db, owner_id, class_id, start)
    if at is None or at == start:
        return None
    row = (await db.execute(_MUSIC, {"owner_id": owner_id, "class_id": class_id, "id": at})).first()
    found = None if row is None else _music(row.music)
    if found is None or found.fingerprint != fingerprint:
        return None  # not the music this draft was edited on
    return found.edit_map


async def load(db: AsyncSession, owner_id: UUID, class_id: UUID) -> SpotifyDraft | str:
    """The current Spotify draft, or a reason (see the module docstring)."""
    row = (await db.execute(_SELECT, {"owner_id": owner_id, "class_id": class_id})).first()
    if row is None:
        return "not_found"
    if row.current is None or row.kind != "draft" or not isinstance(row.content, dict):
        return NO_SPOTIFY_DRAFT
    music = _music(row.music)
    if music is None:
        return NO_SPOTIFY_DRAFT
    content: dict[str, Any] = row.content  # pyright: ignore[reportUnknownVariableType]
    stored = bytes(row.content_hash)
    try:
        overlay = parsed(row.edit_overlay)
    except ValueError:
        return "internal"
    if revision_hash(content, overlay=row.edit_overlay) != stored:
        return "internal"
    edit_map = music.edit_map
    if edit_map is None and row.origin == USER_EDIT_ORIGIN:
        edit_map = await _ancestors_map(db, owner_id, class_id, row.current, music.fingerprint)
    if edit_map is None:
        return EDIT_UNAVAILABLE
    return SpotifyDraft(row.current, stored, content, music, overlay, edit_map)
