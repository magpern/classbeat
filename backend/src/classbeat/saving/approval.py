"""The approval of a Save or an Update and its revalidation (ADR 0007 section 5).

The approval is the user's click on Save for one revision: ``{class_id, revision_id,
content_hash}`` with the MOWL connection the page shows. Nothing written to MOWL comes from the
browser: the spec is rebuilt from the stored revision, the playlist id from the user's own link
row the revision names. The same checks run at creation (``admit``, in the jobs API's
transaction) and again in the worker's ``prepare`` (a fenced transaction, after ``hold``), each
against the authenticated user and failing closed:

- the class and the revision are the user's own and the revision belongs to that class (looked
  up with ``owner_id`` and ``class_id`` together: a foreign or unknown id, or a revision of
  another class, is ``invalid_input``, never a different answer), and it is a ``draft``;
- its stored ``content_hash`` equals the approved one (``hash_mismatch``) and the SHA-256 of its
  canonical content recomputed now (``invalid_input``: the row is not what was hashed);
- the content is a ``WorkoutDraft`` whose spec passes ``workout.validate`` (``invalid_input``);
- the revision's ``music.link_id`` names a ``mowl_playlist_links`` row of the user of the
  revision's source (``spotify`` for a playlist, ``swedebeat`` for a mix, ADR 0016 section 3)
  (``music_unlinked``: the MOWL account changed since the revision was designed, section 9).

For an Update (``classbeat.saving.update``) also ``saved_target``: the saved row is the user's
own live row of that class (``invalid_input`` otherwise, section 9: a detached row is never
written to), and its program id is the only program an Update may write.

The connection checks (``not_connected``) and the unresolved-write rule (``write_unresolved``,
``classbeat.operations.orphans``) are made by the callers next to these, in the same transaction.
Reasons are codes only; nothing of the row is logged.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from mowl import workout
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.schemas import WorkoutDraft
from classbeat.context import UserContext
from classbeat.design.revision import MIX_SOURCE, MUSIC_SOURCE, REVISION_MUSIC, canonical
from classbeat.operations.attach import SOURCE
from classbeat.operations.swedebeat import SOURCE as MIX_LINK_SOURCE

HEX_SHA256: Final = r"^[0-9a-f]{64}$"
# The revision's music source -> the source of the link row it names.
LINK_SOURCES: Final = {MUSIC_SOURCE: SOURCE, MIX_SOURCE: MIX_LINK_SOURCE}
MAX_PLAYLIST_ID: Final = 2**63 - 1
MAX_PROGRAM_ID: Final = 2**63 - 1

_REVISION = text(
    "SELECT r.id, r.class_id, r.number, r.kind, r.content, r.content_hash, r.music "
    "FROM class_revisions r JOIN classes c ON c.owner_id = r.owner_id AND c.id = r.class_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.id = :revision_id"
)
_LINK = text(
    "SELECT id, playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND id = :link_id AND source = :source"
)


@dataclass(frozen=True, slots=True)
class Revision:
    """A revision that passed the checks: what a save writes, all from the stored row."""

    class_id: UUID
    revision_id: UUID
    number: int
    content_hash: bytes
    spec: dict[str, Any]
    link_id: UUID
    link_source: str = SOURCE  # ``mowl_playlist_links.source`` of the link (``LINK_SOURCES``)

    @property
    def content_hash_hex(self) -> str:
        return self.content_hash.hex()

    @property
    def segments(self) -> int:
        return len(self.spec["segments"])


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str  # invalid_input, hash_mismatch, music_unlinked


def spec_of(content: object) -> dict[str, Any] | None:
    """The library spec of a stored draft, if it still validates (``WorkoutDraft`` from its JSON,
    as the dispatcher validates the model's draft, then ``workout.validate``); else None."""
    try:
        draft = WorkoutDraft.model_validate_json(json.dumps(content))
        spec = draft.to_spec()
        workout.validate(spec)
    except (ValidationError, ValueError, TypeError, KeyError):
        return None
    return spec


async def revision(
    db: AsyncSession, ctx: UserContext, class_id: UUID, revision_id: UUID, content_hash: bytes
) -> Revision | Refusal:
    """The revision checks of the module docstring (all but the link)."""
    row = (
        await db.execute(
            _REVISION,
            {"owner_id": ctx.user_id, "class_id": class_id, "revision_id": revision_id},
        )
    ).first()
    if row is None or row.kind != "draft":
        return Refusal("invalid_input")
    stored = bytes(row.content_hash)
    if stored != content_hash:
        return Refusal("hash_mismatch")
    if hashlib.sha256(canonical(row.content)).digest() != stored:
        return Refusal("invalid_input")
    spec = spec_of(row.content)
    if spec is None:
        return Refusal("invalid_input")
    try:
        music = REVISION_MUSIC.validate_json(json.dumps(row.music))
    except ValidationError:
        return Refusal("music_unlinked")
    return Revision(
        class_id=row.class_id,
        revision_id=row.id,
        number=row.number,
        content_hash=stored,
        spec=spec,
        link_id=music.link_id,
        link_source=LINK_SOURCES[music.source],
    )


async def linked_playlist(
    db: AsyncSession, ctx: UserContext, link_id: UUID, source: str = SOURCE
) -> int | None:
    """The MOWL playlist id of the user's link row the revision names (of ``source``: the
    revision's, ``Revision.link_source``), or None when it is gone (a disconnect or reconnect
    deleted it). Read under ``hold`` on the worker path."""
    row = (
        await db.execute(_LINK, {"owner_id": ctx.user_id, "link_id": link_id, "source": source})
    ).first()
    if row is None:
        return None
    playlist_id = row.playlist_id
    if not isinstance(playlist_id, int) or not 0 < playlist_id <= MAX_PLAYLIST_ID:
        return None
    return playlist_id


# Update's target (ADR 0007 section 7): the user's own **live** saved row **of this class**, and
# its creating operation, when it still names one, of the same class too (the foreign key is
# owner-inclusive, not class-inclusive: #156 review). The program id is read only from here.
_SAVED_TARGET = text(
    "SELECT s.id, s.program_id, s.state_hash FROM saved_workouts s "
    "WHERE s.owner_id = :owner_id AND s.id = :saved_workout_id AND s.class_id = :class_id "
    "AND s.detached_at IS NULL "
    "AND (s.operation_id IS NULL OR EXISTS (SELECT 1 FROM external_operations o "
    "WHERE o.owner_id = s.owner_id AND o.id = s.operation_id AND o.class_id = s.class_id))"
)


@dataclass(frozen=True, slots=True)
class SavedTarget:
    """The program an Update may write, from the user's own live saved row."""

    saved_workout_id: UUID
    program_id: int
    state_hash: bytes | None


async def saved_target(
    db: AsyncSession, ctx: UserContext, class_id: UUID, saved_workout_id: UUID
) -> SavedTarget | None:
    """The live saved row ``saved_workout_id`` of the user's class ``class_id``, else None (another
    user's, another class's, a detached or an unknown row: all the same answer)."""
    row = (
        await db.execute(
            _SAVED_TARGET,
            {
                "owner_id": ctx.user_id,
                "saved_workout_id": saved_workout_id,
                "class_id": class_id,
            },
        )
    ).first()
    if row is None:
        return None
    program_id = row.program_id
    if not isinstance(program_id, int) or not 0 < program_id <= MAX_PROGRAM_ID:
        return None
    state = None if row.state_hash is None else bytes(row.state_hash)
    return SavedTarget(row.id, program_id, state)
