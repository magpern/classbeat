"""The ``swedebeat_match`` revision (ADR 0016 section 5, B1): one ``song_list`` matched against
Swedebeat's catalogue.

A ``class_revisions`` row with ``kind = 'swedebeat_match'``: immutable (ADR 0007 section 2), never a
class's current revision, never revised or saved. ``parent_id`` is the matched ``song_list`` of the
same class.

- **content** (``MatchContent``, ``v: 1``, at most 64 KB as canonical JSON)::

      {"v": 1, "song_list_hash": "<hex sha-256 of the list's content>",
       "songs": [{"position": 1, "status": "found" | "unsure" | "missing",
                  "wanted": {"title": "...", "artist": "..."},
                  "swedebeat": {"title": "...", "artist": "...", "duration": 241} | null}],
       "found": 6, "unsure": 1, "missing": 1, "swedebeat_sec": 1501}

  ``wanted`` is the list's song (Spotify-derived), ``swedebeat`` the best catalogue hit as the
  parser kept it (``null`` exactly when ``missing``; ``duration`` in whole seconds or ``null``).
  ``swedebeat_sec`` is the sum of the **found** songs' durations (an ``unsure`` song is never
  counted). No catalogue id, URL or token: the parser never keeps one.
- **content_hash** = SHA-256 of the content's canonical JSON
  (``classbeat.design.revision.canonical``).
- **music** = ``{source: "swedebeat_catalogue", song_list_hash}``; ``song_list_hash`` binds the
  match to the exact list it was made from (B3 compares it before approving a selection).
- **provenance** = ``{origin: "swedebeat_match", matcher_version, threshold}``: no model ran.

Nothing here is logged; the titles never reach a model (ADR 0016 section 6: statuses only), an
event or a log.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.revision import MAX_CONTENT_BYTES, canonical
from classbeat.jobs import FencedSession, JobFailure
from classbeat.swedebeat_match import catalogue

KIND: Final = "swedebeat_match"
VERSION: Final = 1
MATCHER_VERSION: Final = 1
MUSIC_SOURCE: Final = "swedebeat_catalogue"
MAX_SONGS: Final = 40  # ADR 0016 section 5: at most 40 songs per job (a song list's own limit)

SongText = Annotated[str, Field(max_length=catalogue.MAX_TEXT)]
HitText = Annotated[str, Field(min_length=1, max_length=catalogue.MAX_TEXT)]


class Wanted(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: SongText
    artist: SongText


class Matched(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: HitText
    artist: HitText
    duration: Annotated[int, Field(ge=1, le=catalogue.MAX_DURATION)] | None


class MatchedSong(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    position: int = Field(ge=1, le=MAX_SONGS)
    status: Literal["found", "unsure", "missing"]
    wanted: Wanted
    swedebeat: Matched | None

    @model_validator(mode="after")
    def _hit_unless_missing(self) -> Self:
        if (self.swedebeat is None) != (self.status == "missing"):
            raise ValueError("a found or unsure song has its hit, a missing one none")
        return self


class MatchContent(BaseModel):
    """``class_revisions.content`` of a ``swedebeat_match``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    v: Literal[1]
    song_list_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    songs: list[MatchedSong] = Field(min_length=1, max_length=MAX_SONGS)
    found: int = Field(ge=0)
    unsure: int = Field(ge=0)
    missing: int = Field(ge=0)
    swedebeat_sec: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if [s.position for s in self.songs] != list(range(1, len(self.songs) + 1)):
            raise ValueError("songs are in play order, numbered from 1")
        statuses = [s.status for s in self.songs]
        counts = (statuses.count("found"), statuses.count("unsure"), statuses.count("missing"))
        if counts != (self.found, self.unsure, self.missing):
            raise ValueError("the totals count the songs")
        if self.swedebeat_sec != found_seconds(self.songs):
            raise ValueError("swedebeat_sec is the found songs' length")
        return self


class MatchMusic(BaseModel):
    """``class_revisions.music`` of a ``swedebeat_match``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["swedebeat_catalogue"]
    song_list_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def found_seconds(songs: list[MatchedSong]) -> int:
    return sum(
        s.swedebeat.duration
        for s in songs
        if s.status == "found" and s.swedebeat is not None and s.swedebeat.duration is not None
    )


def content_hash(content: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonical(content)).digest()


@dataclass(frozen=True)
class MatchRevision:
    """A match to store (``build``) or as read back (``load``)."""

    content: MatchContent
    content_hash: bytes
    music: MatchMusic
    provenance: dict[str, Any]
    song_list_id: UUID

    async def store(self, fs: FencedSession) -> UUID:
        """Insert the revision (the job's own class, read from the fenced job row) under its
        ``song_list``, which must still be that class's list with the hash the match was made
        from; else ``invalid_input`` and nothing is stored. Then its ``revision_created``."""
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
                    "parent_id": self.song_list_id,
                    "list_hash": bytes.fromhex(self.content.song_list_hash),
                    "job_id": fs.lease.job_id,
                    "owner_id": fs.lease.owner_id,
                },
            )
        ).first()
        if found is None:  # the list is gone, or not the job's class's
            raise JobFailure("invalid_input")
        await fs.event(
            "revision_created", {"revision_id": revision_id, "kind": KIND, "number": found.number}
        )
        return revision_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


_INSERT = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id, parent_id) "
    "SELECT :id, j.owner_id, j.class_id, coalesce((SELECT max(r.number) FROM class_revisions r "
    "WHERE r.owner_id = j.owner_id AND r.class_id = j.class_id), 0) + 1, 'swedebeat_match', "
    "CAST(:content AS jsonb), :content_hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
    "j.id, p.id FROM jobs j JOIN class_revisions p ON p.id = :parent_id "
    "AND p.owner_id = j.owner_id AND p.class_id = j.class_id AND p.kind = 'song_list' "
    "AND p.content_hash = :list_hash "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id RETURNING number"
)
_LOAD = text(
    "SELECT content, content_hash, music, provenance, parent_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id "
    "AND kind = 'swedebeat_match'"
)


def build(songs: list[MatchedSong], *, song_list_id: UUID, song_list_hash: bytes) -> MatchRevision:
    """The revision of a finished match.

    ``invalid_output`` if it does not fit its rules or 64 KB.
    """
    try:
        content = MatchContent(
            v=VERSION,
            song_list_hash=song_list_hash.hex(),
            songs=songs,
            found=sum(s.status == "found" for s in songs),
            unsure=sum(s.status == "unsure" for s in songs),
            missing=sum(s.status == "missing" for s in songs),
            swedebeat_sec=found_seconds(songs),
        )
        music = MatchMusic(source=MUSIC_SOURCE, song_list_hash=song_list_hash.hex())
    except ValidationError:
        raise JobFailure("invalid_output") from None
    dumped = content.model_dump(mode="json")
    if len(canonical(dumped)) > MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    provenance = {
        "origin": KIND,
        "matcher_version": MATCHER_VERSION,
        "threshold": catalogue.SAME_SONG,
    }
    return MatchRevision(content, content_hash(dumped), music, provenance, song_list_id)


async def load(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID
) -> MatchRevision | None:
    """The user's match of that class, with its hash re-checked: None when there is none, when its
    stored hash is not the SHA-256 of its content now, or when it no longer parses."""
    row = (
        await db.execute(_LOAD, {"owner_id": owner_id, "class_id": class_id, "id": revision_id})
    ).first()
    if row is None or not isinstance(row.content, dict) or row.parent_id is None:
        return None
    raw: dict[str, Any] = row.content  # pyright: ignore[reportUnknownVariableType]
    stored = bytes(row.content_hash)
    if content_hash(raw) != stored:
        return None
    try:
        content = MatchContent.model_validate(raw)
        music = MatchMusic.model_validate(row.music)
    except ValidationError:
        return None
    if music.song_list_hash != content.song_list_hash:
        return None
    provenance: dict[str, Any] = row.provenance if isinstance(row.provenance, dict) else {}  # pyright: ignore[reportUnknownVariableType]
    return MatchRevision(content, stored, music, provenance, row.parent_id)
