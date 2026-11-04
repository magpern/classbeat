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
- the class's sport is the class's stored brief's (ADR 0018 section 1, M8 R1b): absent (every
  class stored before R1a) or ``cycling`` is cycling, ``running`` is running, anything else is
  ``invalid_input``. Never the browser's, a model's or the program's in MOWL;
- the content is the sport's draft (``WorkoutDraft`` for cycling, ``RunningDraft`` for running,
  ``agent.schemas.draft_model``, never the other one) whose spec passes ``workout.validate``
  (which validates a running spec as running) (``invalid_input``). A running spec carries the
  library's ``"sport": "running"`` (``RunningDraft.to_spec``), a cycling one no ``"sport"``, so
  the spec's sport is always the brief's;
- a running revision was designed on an existing MOWL playlist (ADR 0018 section 2, Amendment
  2): its music is a ``mowl_spotify_playlist`` without picked songs; a Swedebeat mix or a
  ``song_list_id`` is ``invalid_input`` (the library would refuse new music anyway, and Save never
  attaches anything);
- the revision's music is ``{"source": "none"}`` (a soundless class, ADR 0007 amendment of
  2026-11-03, items 1 and 8) **exactly when** the class's stored ``music_source`` is ``none``
  (``invalid_input`` otherwise, both ways: Save keys on both, and the two must agree); a running
  class with a ``none`` revision is ``invalid_input`` (running v1 above);
- the revision's ``music.link_id`` names a ``mowl_playlist_links`` row of the user of the
  revision's source (``spotify`` for a playlist, ``swedebeat`` for a mix, ADR 0016 section 3)
  (``music_unlinked``: the MOWL account changed since the revision was designed, section 9).
  A soundless revision names no link: none is read, ``music_unlinked`` cannot happen, and the
  playlist written is the library's "no music", ``NO_PLAYLIST`` (``PlaylistID: -1``;
  ``playlist_for``).

The ``running_enabled`` flag is not checked here (the reconcilers rebuild the spec through
``revision`` and must still decide a running write when the flag is off): the Save and Update
kinds check it next to these, ``running_refused``. Likewise the soundless switch
(``CLASSBEAT_SOUNDLESS_ENABLED`` and a stored ``soundless_enabled`` of exactly ``on``; no
coverage check, Save calls no model): ``soundless_refused``, at creation and in ``prepare`` only.

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
from typing import Annotated, Any, Final
from uuid import UUID

from mowl import workout
from pydantic import Field, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.schemas import draft_model
from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.design import sport as class_sport
from classbeat.design.revision import (
    MIX_SOURCE,
    MUSIC_SOURCE,
    NoRevisionMusic,
    RevisionMusic,
    SpotifyRevisionMusic,
    canonical,
)
from classbeat.flags import effective
from classbeat.operations.attach import SOURCE
from classbeat.operations.swedebeat import SOURCE as MIX_LINK_SOURCE

HEX_SHA256: Final = r"^[0-9a-f]{64}$"
# The revision's music source -> the source of the link row it names.
LINK_SOURCES: Final = {MUSIC_SOURCE: SOURCE, MIX_SOURCE: MIX_LINK_SOURCE}
MAX_PLAYLIST_ID: Final = 2**63 - 1
MAX_PROGRAM_ID: Final = 2**63 - 1
# The library's "no music" (``workout.save``'s default ``playlist_id``, sent as ``PlaylistID``):
# what a soundless class is saved and updated with. Never stored: ``saved_workouts.playlist_id``
# is NULL for it (its CHECK is ``> 0``).
NO_PLAYLIST: Final = -1
# What Save reads from ``class_revisions.music``: the two music shapes and the soundless one.
SAVED_MUSIC: Final = TypeAdapter[RevisionMusic | NoRevisionMusic](
    Annotated[RevisionMusic | NoRevisionMusic, Field(discriminator="source")]
)

_REVISION = text(
    "SELECT r.id, r.class_id, r.number, r.kind, r.content, r.content_hash, r.music, "
    "c.brief ->> 'sport' AS sport, c.music_source FROM class_revisions r "
    "JOIN classes c ON c.owner_id = r.owner_id AND c.id = r.class_id "
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
    link_id: UUID | None  # None only for a soundless revision, which names no link
    link_source: str = SOURCE  # ``mowl_playlist_links.source`` of the link (``LINK_SOURCES``)
    sport: str = class_sport.CYCLING  # the class's stored brief's (``sport_of_brief``)

    @property
    def running(self) -> bool:
        return self.sport == class_sport.RUNNING

    @property
    def soundless(self) -> bool:
        """A revision whose music is ``none`` (and so, checked, a class whose source is)."""
        return self.link_id is None

    @property
    def content_hash_hex(self) -> str:
        return self.content_hash.hex()

    @property
    def segments(self) -> int:
        return len(self.spec["segments"])


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str  # invalid_input, hash_mismatch, music_unlinked


SPORTS: Final = (class_sport.CYCLING, class_sport.RUNNING)


def sport_of_brief(value: object) -> str | None:
    """The class's sport from ``classes.brief ->> 'sport'``: absent (NULL) is cycling, as every
    class stored before ADR 0018; ``cycling`` or ``running`` exactly; None for anything else."""
    if value is None:
        return class_sport.CYCLING
    return value if isinstance(value, str) and value in SPORTS else None


def spec_of(content: object, sport: str = class_sport.CYCLING) -> dict[str, Any] | None:
    """The library spec of a stored draft of ``sport``, if it still validates (the sport's draft
    schema from its JSON, as the dispatcher validates the model's draft, then
    ``workout.validate``, which checks a running spec as running); else None. The spec's own
    sport must be ``sport`` (a cycling spec has no ``"sport"``)."""
    try:
        draft = draft_model(sport).model_validate_json(json.dumps(content))
        spec = draft.to_spec()
        workout.validate(spec)
        if workout.sport_of(spec) != sport:
            return None
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
    sport = sport_of_brief(row.sport)
    if sport is None:
        return Refusal("invalid_input")  # a brief whose sport is neither: never guessed
    stored = bytes(row.content_hash)
    if stored != content_hash:
        return Refusal("hash_mismatch")
    if hashlib.sha256(canonical(row.content)).digest() != stored:
        return Refusal("invalid_input")
    spec = spec_of(row.content, sport)
    if spec is None:
        return Refusal("invalid_input")
    try:
        music = SAVED_MUSIC.validate_json(json.dumps(row.music))
    except ValidationError:
        return Refusal("music_unlinked")
    if sport == class_sport.RUNNING and not (
        isinstance(music, SpotifyRevisionMusic) and music.song_list_id is None
    ):
        return Refusal("invalid_input")  # running v1: an existing MOWL playlist only
    soundless = isinstance(music, NoRevisionMusic)
    if soundless != music_kind.is_none(row.music_source):
        return Refusal("invalid_input")  # the revision's music and the class's must agree
    if isinstance(music, NoRevisionMusic):
        return Revision(
            class_id=row.class_id,
            revision_id=row.id,
            number=row.number,
            content_hash=stored,
            spec=spec,
            link_id=None,
            sport=sport,
        )
    return Revision(
        class_id=row.class_id,
        revision_id=row.id,
        number=row.number,
        content_hash=stored,
        spec=spec,
        link_id=music.link_id,
        link_source=LINK_SOURCES[music.source],
        sport=sport,
    )


async def running_refused(db: AsyncSession, found: Revision) -> bool:
    """Whether Save or Update must refuse ``feature_off``: the class is running and
    ``running_enabled`` (the environment's switch and the admin flag, stricter wins) is off now
    (ADR 0018 section 5). A cycling class never reads the flag."""
    return found.running and not await class_sport.running_on(db)


async def soundless_refused(db: AsyncSession, found: Revision) -> bool:
    """Whether Save or Update must refuse ``feature_off``: the revision is soundless and the
    soundless switch (``CLASSBEAT_SOUNDLESS_ENABLED`` and a stored ``soundless_enabled`` of
    exactly ``on``) is off now (ADR 0007 amendment of 2026-11-03, item 2). Coverage is not read:
    no model runs. A class with music never reads the flag; the reconcilers never call this."""
    if not found.soundless:
        return False
    return not await effective.soundless_switch(db, music_kind.current().enabled)


async def playlist_for(db: AsyncSession, ctx: UserContext, found: Revision) -> int | None:
    """The ``PlaylistID`` a Save or Update of ``found`` writes: ``NO_PLAYLIST`` for a soundless
    revision (no link row is read), else the user's link row's playlist (``linked_playlist``;
    None when it is gone: ``music_unlinked``)."""
    if found.link_id is None:
        return NO_PLAYLIST
    return await linked_playlist(db, ctx, found.link_id, found.link_source)


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
