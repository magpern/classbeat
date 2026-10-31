"""The ``song_list`` revision (ADR 0008 section 5): what the user approves, once, by its hash.

A ``class_revisions`` row with ``kind = 'song_list'``: immutable (ADR 0007 section 2), never a
class's current revision, never revised or saved as a draft.

- **content** (``SongListContent``, version ``v: 2``, at most 64 KB as canonical JSON)::

      {"v": 2, "mode": "pick" | "replace", "target_sec": 2700, "total_sec": 2688,
       "crossfade_sec": 9.0,
       "songs": [{"spotify_id": "...", "title": "...", "artist": "...", "role": "climb",
                  "bpm": 92.0, "length_sec": 241, "new": true,
                  "cover": "https://i.scdn.co/image/<40 hex>" | null}],
       "omitted_no_bpm": 2, "note": "..."}

  ``v: 2`` (ADR 0013 section 9) adds ``cover``, the album cover URL from the search hit (or the
  kept song's stored one), kept only when ``classbeat.covers.cover_url`` accepts it and ``null``
  otherwise; it is checked again whenever content is parsed, so a stored URL that no longer
  passes reads as ``null``. ``v: 1`` content (no ``cover`` key) stays readable and reads as no
  cover. The hash covers ``cover`` like every other field.

  Built from ``classbeat.agent.ChosenList``, which ``choose_songs`` builds from the analysed pool:
  every id, title, artist, BPM and length is MOWL's analysis or a kept song's stored value, never
  the model's words; the order is the approved play order. BPM is rounded to 0.1.
- **content_hash** = SHA-256 of the content's canonical JSON (sorted keys, no spaces, UTF-8; the
  same function as drafts, ``classbeat.design.revision.canonical``). It covers every id, title,
  number and the order, so any change to the stored content no longer matches it.
- **music** = ``{source: "claude_pick", fingerprint, spotify_user_id, generations}``:
  ``fingerprint`` is the hex SHA-256 of the ordered Spotify ids
  (``classbeat.playlists.common.ids_hash``, what the final playlist's read-back is compared with
  in phase 2), ``spotify_user_id`` the Spotify account the
  pool was written to and the list made for (``account_changed`` at approval, ADR 0008 section 5),
  ``generations`` the MOWL and Spotify connection generations phase 1 used (the approved phase 2
  refuses ``not_connected`` after a reconnect; a resume does not compare them).
- **provenance** as ADR 0006 section 6, plus ``origin`` and the song stages' ``stage_versions``.
- **parent_id**: the seed list a replace started from (the same class), else NULL.

``load`` reads one back (owner, class, id and kind together; anything else is None) and checks
that its stored hash is still the SHA-256 of its content and its content still parses: the one
reader for the approval check (``classbeat.songs.admit``), the seed of a replace and phase 2's
``prepare`` (issue SJ2).

Nothing here is logged; the ids never reach a model, an event or a page (CL1 shows the songs
without ids).
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.choose_songs import ChosenList
from classbeat.agent.schemas import MAX_SONGS, Role
from classbeat.covers import cover_url
from classbeat.design.revision import MAX_CONTENT_BYTES, canonical
from classbeat.jobs import FencedSession, JobFailure
from classbeat.playlists import common

VERSION: Final = 2
MUSIC_SOURCE: Final = "claude_pick"
MAX_TEXT: Final = 200
MAX_NOTE: Final = 300

SpotifyId = Annotated[str, Field(pattern=r"^[0-9A-Za-z]{22}$")]


class Song(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    spotify_id: SpotifyId
    title: str = Field(max_length=MAX_TEXT)
    artist: str = Field(max_length=MAX_TEXT)
    role: Role
    bpm: float = Field(gt=0, le=400)
    length_sec: int = Field(ge=1, le=7200)
    new: bool
    cover: str | None = None  # v: 2 (ADR 0013 section 9); absent in v: 1

    @field_validator("cover", mode="before")
    @classmethod
    def _pinned_host(cls, value: object) -> str | None:
        """Anything but an album cover URL on the pinned host is ``None``, never an error."""
        return cover_url(value)


class SongListContent(BaseModel):
    """``class_revisions.content`` of a ``song_list``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    v: Literal[1, 2]
    mode: Literal["pick", "replace"]
    target_sec: int = Field(ge=1)
    total_sec: int = Field(ge=0)
    crossfade_sec: float = Field(ge=0, le=12)
    songs: list[Song] = Field(min_length=1, max_length=MAX_SONGS)
    omitted_no_bpm: int = Field(ge=0)
    note: str = Field(max_length=MAX_NOTE)

    @model_validator(mode="after")
    def _cover_is_v2(self) -> "SongListContent":
        if self.v == 1 and any("cover" in s.model_fields_set for s in self.songs):
            raise ValueError("cover is a v: 2 field")
        return self

    def track_ids(self) -> tuple[str, ...]:
        return tuple(s.spotify_id for s in self.songs)


class Generations(BaseModel):
    """The ``generation`` of each connection the list was made with (ADR 0009 section 5.4)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    mowl: int = Field(ge=0)
    spotify: int = Field(ge=0)


class SongListMusic(BaseModel):
    """``class_revisions.music`` of a ``song_list``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["claude_pick"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    spotify_user_id: str = Field(min_length=1, max_length=100)
    # The connections phase 1 used; the approved phase 2 refuses (``not_connected``) when either
    # was reconnected since. A resume uses the connections as they are now (section 6).
    generations: Generations | None = None


def content_hash(content: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonical(content)).digest()


def fingerprint(track_ids: tuple[str, ...]) -> str:
    """The ordered ids' SHA-256 (hex): what the final playlist must hold, in this order."""
    return common.ids_hash(common.check_track_ids(track_ids)).hex()


@dataclass(frozen=True)
class SongListRevision:
    """A ``song_list`` revision to store (``build``) or as read back (``load``)."""

    content: SongListContent
    content_hash: bytes
    music: SongListMusic
    provenance: dict[str, Any]
    parent_id: UUID | None = None

    @property
    def track_ids(self) -> tuple[str, ...]:
        return self.content.track_ids()

    @property
    def account(self) -> str:
        return self.music.spotify_user_id

    async def store(self, fs: FencedSession) -> UUID:
        """Insert the revision (the job's own class, read from the fenced job row; the parent,
        when given, must be a revision of that class) and its ``revision_created`` event."""
        revision_id = uuid.uuid4()
        found = (
            await fs.db.execute(
                _INSERT,
                {
                    "id": revision_id,
                    "content": _json(self.content.model_dump(mode="json")),
                    "content_hash": self.content_hash,
                    "music": _json(self.music.model_dump(mode="json")),
                    "provenance": json.dumps(self.provenance, sort_keys=True),
                    "parent_id": self.parent_id,
                    "job_id": fs.lease.job_id,
                    "owner_id": fs.lease.owner_id,
                },
            )
        ).first()
        if found is None:  # the job's row or the parent is not what the input said
            raise JobFailure("internal")
        await fs.event(
            "revision_created",
            {"revision_id": revision_id, "kind": "song_list", "number": found.number},
        )
        return revision_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# As the draft's insert (classbeat.design.revision), with kind 'song_list'.
_INSERT = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id, parent_id) "
    "SELECT :id, j.owner_id, j.class_id, coalesce((SELECT max(r.number) FROM class_revisions r "
    "WHERE r.owner_id = j.owner_id AND r.class_id = j.class_id), 0) + 1, 'song_list', "
    "CAST(:content AS jsonb), :content_hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
    "j.id, p.id FROM jobs j LEFT JOIN class_revisions p ON p.id = CAST(:parent_id AS uuid) "
    "AND p.owner_id = j.owner_id AND p.class_id = j.class_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id "
    "AND (CAST(:parent_id AS uuid) IS NULL OR p.id IS NOT NULL) RETURNING number"
)
_LOAD = text(
    "SELECT content, content_hash, music, provenance, parent_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'song_list'"
)


def build(
    chosen: ChosenList,
    *,
    account: str,
    provenance: dict[str, Any],
    parent_id: UUID | None = None,
    generations: Generations | None = None,
) -> SongListRevision:
    """The revision of ``chosen`` for the Spotify account ``account``; ``invalid_output`` if it
    does not fit the content's rules or 64 KB."""
    try:
        content = SongListContent(
            v=VERSION,
            mode="replace" if chosen.mode == "replace" else "pick",
            target_sec=chosen.target_sec,
            total_sec=chosen.total_sec,
            crossfade_sec=float(chosen.crossfade_sec),
            songs=[
                Song(
                    spotify_id=s.spotify_id,
                    title=s.title[:MAX_TEXT],
                    artist=s.artist[:MAX_TEXT],
                    role=s.role,  # pyright: ignore[reportArgumentType] - validated by Role
                    bpm=round(float(s.bpm), 1),
                    length_sec=int(s.length_sec),
                    new=s.new,
                    cover=cover_url(s.cover),
                )
                for s in chosen.songs
            ],
            omitted_no_bpm=chosen.omitted_no_bpm,
            note=chosen.note[:MAX_NOTE],
        )
        dumped = content.model_dump(mode="json")
        music = SongListMusic(
            source=MUSIC_SOURCE,
            fingerprint=fingerprint(content.track_ids()),
            spotify_user_id=account,
            generations=generations,
        )
    except ValidationError:
        raise JobFailure("invalid_output") from None
    if len(canonical(dumped)) > MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    return SongListRevision(content, content_hash(dumped), music, provenance, parent_id)


async def load(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID
) -> SongListRevision | None:
    """The user's ``song_list`` revision of that class, with its hash re-checked: None when there
    is none, when its stored hash is not the SHA-256 of its content now, or when the content or
    the music no longer parse (each is a refusal for the caller, never a different answer)."""
    row = (
        await db.execute(_LOAD, {"owner_id": owner_id, "class_id": class_id, "id": revision_id})
    ).first()
    if row is None or not isinstance(row.content, dict):
        return None
    raw: dict[str, Any] = row.content  # pyright: ignore[reportUnknownVariableType]
    stored = bytes(row.content_hash)
    if content_hash(raw) != stored:
        return None
    try:
        content = SongListContent.model_validate(raw)
        music = SongListMusic.model_validate(row.music)
    except ValidationError:
        return None
    if music.fingerprint != fingerprint(content.track_ids()):
        return None
    provenance: dict[str, Any] = row.provenance if isinstance(row.provenance, dict) else {}  # pyright: ignore[reportUnknownVariableType]
    return SongListRevision(content, stored, music, provenance, row.parent_id)
