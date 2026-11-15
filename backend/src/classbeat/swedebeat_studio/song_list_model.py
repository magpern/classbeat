"""A studio ``song_list`` revision's content and music (ADR 0036 sections 1 and 3). Pure: no I/O.

The content (``StudioListContent``, ``v: 1``, ``source: "swedebeat_studio"``)::

    {"v": 1, "source": "swedebeat_studio", "mode": "pick" | "replace",
     "target_sec": 2700, "total_sec": 2650, "crossfade_sec": 9.0,
     "songs": [{"spotify_id": "...", "title": "...", "artist": "...", "role": "climb",
                "new": true, "cover": "https://i.scdn.co/image/<40 hex>" | null,
                "gap": false,
                "swedebeat": {"reference": "...", "title": "...", "artist": "...",
                              "duration_sec": 241, "bpm": 128.0, "score": 0.93} | null,
                "swap": {"original": {"spotify_id": "...", "title": "...", "artist": "...",
                                      "cover": null},
                         "replacement": {"title": "...", "artist": "..."},
                         "reason": "missing" | "unsure" | "asked", "score": 0.93,
                         "undone": false} | null}],
     "evidence": {"state": "ok" | "suspected_unavailable", "sent": 14, "missing_first": 3,
                  "missing": 2, "anyway": false},
     "rounds": 1, "note": "..."}

- ``title`` and ``artist`` are the Spotify search hit's (what the user picked from); ``swedebeat``
  holds the gateway match's facts: the catalogue ``reference`` (the download's key, owned, deleted
  with the class, never a model's input), SwedeBeat's own title, artist, ``duration_sec`` and
  ``bpm``, and the match ``score`` (at least ``SAME_SONG``: only a found song has facts).
- **A gap** (``gap: true``) has no ``swedebeat`` facts: an undone swap (its ``swap.undone``) or a
  song of a ``suspected_unavailable`` list. A gap can never be approved into a mix
  (``swap_policy.mix_tracks``, the kind's approval check).
- ``total_sec`` is the backend's: the non-gap songs' SwedeBeat durations less ``crossfade_sec``
  per change of song (``length_policy``).
- The content hash (``song_list.content_hash``, the canonical JSON's SHA-256) covers every field,
  the references, durations and BPMs included, so the approval binds them.

The music (``StudioListMusic``): ``source``, ``fingerprint`` (the ordered Spotify ids' hash, as
``songs.song_list``), the Spotify account, the SwedeBeat connection, and both connections'
generations.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from classbeat.agent.schemas import MAX_SONGS, Role
from classbeat.covers import cover_url

VERSION: Final = 1
SOURCE: Final = "swedebeat_studio"
MAX_TEXT: Final = 200
MAX_NOTE: Final = 300
MAX_ROUNDS: Final = 3

SpotifyId = Annotated[str, Field(pattern=r"^[0-9A-Za-z]{22}$")]
Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Text = Annotated[str, Field(max_length=MAX_TEXT)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Facts(_Model):
    """A found song's SwedeBeat facts, from the gateway match (never from MOWL or a model)."""

    reference: Reference
    title: Text
    artist: Text
    duration_sec: int = Field(ge=1, le=3600)
    bpm: float = Field(gt=0, le=400)
    score: float = Field(ge=0, le=1)


class Original(_Model):
    """The song a swap replaced, as it was wanted: kept so an undo can restore it as a gap."""

    spotify_id: SpotifyId
    title: Text
    artist: Text
    cover: str | None = None

    @field_validator("cover", mode="before")
    @classmethod
    def _pinned_host(cls, value: object) -> str | None:
        return cover_url(value)


class Named(_Model):
    title: Text
    artist: Text


class Swap(_Model):
    original: Original
    replacement: Named
    reason: Literal["missing", "unsure", "asked"]
    score: float = Field(ge=0, le=1)
    undone: bool = False


class StudioSong(_Model):
    spotify_id: SpotifyId
    title: Text
    artist: Text
    role: Role
    new: bool
    cover: str | None = None
    gap: bool
    swedebeat: Facts | None
    swap: Swap | None = None

    @field_validator("cover", mode="before")
    @classmethod
    def _pinned_host(cls, value: object) -> str | None:
        return cover_url(value)

    @model_validator(mode="after")
    def _gap_has_no_facts(self) -> Self:
        if self.gap != (self.swedebeat is None):
            raise ValueError("a gap is exactly a song without SwedeBeat facts")
        if self.swap is not None and self.swap.undone != self.gap:
            raise ValueError("an undone swap is a gap, and a gap's swap is undone")
        return self


class Evidence(_Model):
    state: Literal["ok", "suspected_unavailable"]
    sent: int = Field(ge=0)
    missing_first: int = Field(ge=0)
    missing: int = Field(ge=0)
    # The match was suspected and the user chose Swap anyway: the list is ``ok`` by that choice.
    anyway: bool = False

    @model_validator(mode="after")
    def _anyway_is_ok(self) -> Self:
        if self.anyway and self.state != "ok":
            raise ValueError("a list swapped anyway is ok by the user's choice")
        return self


class StudioListContent(_Model):
    """``class_revisions.content`` of a studio ``song_list``."""

    v: Literal[1]
    source: Literal["swedebeat_studio"]
    mode: Literal["pick", "replace"]
    target_sec: int = Field(ge=1)
    total_sec: int = Field(ge=0)
    crossfade_sec: float = Field(ge=0, le=12)
    songs: list[StudioSong] = Field(min_length=1, max_length=MAX_SONGS)
    evidence: Evidence
    rounds: int = Field(ge=0, le=MAX_ROUNDS)
    note: str = Field(max_length=MAX_NOTE)

    @model_validator(mode="after")
    def _suspected_has_only_gaps(self) -> Self:
        if self.evidence.state == "suspected_unavailable" and not all(s.gap for s in self.songs):
            raise ValueError("a suspected list holds no matched song")
        return self

    def track_ids(self) -> tuple[str, ...]:
        return tuple(s.spotify_id for s in self.songs)

    def has_gap(self) -> bool:
        return any(s.gap for s in self.songs)


class Generations(_Model):
    spotify: int = Field(ge=0)
    swedebeat: int = Field(ge=0)


class StudioListMusic(_Model):
    """``class_revisions.music`` of a studio ``song_list``."""

    source: Literal["swedebeat_studio"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    spotify_user_id: str = Field(min_length=1, max_length=100)
    swedebeat_connection_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    generations: Generations
