"""The synthetic world of the song cases (ADR 0008 section 3, ADR 0012 section 4.1): the invented
catalogue a pick searches, the MOWL analysis of the tracks it picked and the songs a replace case
keeps. Split from ``pick`` (the cases, the runner and the checks), which re-exports every name.

- ``CatalogueSpec`` / ``ReplaceSpec``: the parts of a case file that describe the world (titles,
  artists and albums, tracks without a BPM, search hits that are broken, a replace case's seed).
- ``SyntheticCatalogue``: the ``Searcher`` the pool stage is given. A query matches a track when
  every word is in its title or artist; ``year:A-B`` in the query filters on the release year.
- ``analysis_map``: the MOWL analysis the choose stage reads, in the library's own format.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from mowl import spotify
from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent.songs import KeptSong

TRACK_SEC: Final = 240
FIRST_BPM: Final = 100
BPM_STEP: Final = 3
SECTION_SEC: Final = 60
LOUDNESS: Final = (-12.0, -6.0, -11.0, -5.0)
CATALOGUE_ID: Final = "SmokePick{:013d}"  # 22 characters
KEPT_ID: Final = "SmokeKept{:013d}"
YEAR_FILTER: Final = re.compile(r"\byear:(\d{4})(?:-(\d{4}))?\b")


Malformed = Literal["no_title", "no_artist", "bad_id", "title_not_text"]


class CatalogueSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    size: int = Field(ge=10, le=120)
    titles: dict[str, str] = Field(default_factory=lambda: dict[str, str]())  # index -> title
    artists: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    albums: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    no_bpm: list[int] = Field(default_factory=lambda: list[int]())  # MOWL has no BPM yet
    # Tracks whose search hit is malformed (see ``CatalogueTrack.hit``): the hit the real
    # tool-result handling (``SearchRegistry.add_hits``) must skip, not a stub's.
    malformed: dict[str, Malformed] = Field(default_factory=lambda: dict[str, Malformed]())


class ReplaceSpec(BaseModel):
    """The seed list's slot roles and the slots to replace; the other slots' songs are kept."""

    model_config = ConfigDict(extra="forbid", strict=True)

    seed_roles: list[str] = Field(min_length=2, max_length=40)
    replace: list[int] = Field(min_length=1)


@dataclass(frozen=True)
class CatalogueTrack:
    spotify_id: str
    title: str
    artist: str
    year: int
    sec: int
    bpm: int  # 0: MOWL has none
    group: str  # a hidden tag, ``group0`` for tracks 0 to 9: a query for it finds that slice
    album: str = "Invented Album"
    malformed: Malformed | None = None  # the hit is returned broken (see ``hit``)

    def hit(self) -> dict[str, Any]:
        """The library's track dict for this track; a ``malformed`` one is broken the way a
        search answer can be (no title, no artist, an id that is no Spotify id, a title that is
        not text), which ``SearchRegistry`` must skip."""
        out: dict[str, Any] = {
            "uri": f"spotify:track:{self.spotify_id}",
            "id": self.spotify_id,
            "title": self.title,
            "artists": [self.artist],
            "album": self.album,
            "year": str(self.year),
            "sec": self.sec,
            "duration_ms": self.sec * 1000,
            "release_date": f"{self.year}-01-01",
        }
        if self.malformed == "no_title":
            del out["title"]
        elif self.malformed == "no_artist":
            out["artists"] = []
        elif self.malformed == "bad_id":
            out["id"] = out["uri"] = "not an id"
        elif self.malformed == "title_not_text":
            out["title"] = {"text": self.title}
        return out


class SyntheticCatalogue:
    """Invented tracks: ``Smoke Song N`` by ``Smoke Artist N // 10``, 240 s, years 2000 to 2024."""

    def __init__(self, spec: CatalogueSpec) -> None:
        self.tracks = [
            CatalogueTrack(
                CATALOGUE_ID.format(i),
                spec.titles.get(str(i), f"Smoke Song {i}"),
                spec.artists.get(str(i), f"Smoke Artist {i // 10}"),
                2000 + i % 25,
                TRACK_SEC,
                0 if i in spec.no_bpm else FIRST_BPM + BPM_STEP * (i % 40),
                f"group{i // 10}",
                spec.albums.get(str(i), "Invented Album"),
                spec.malformed.get(str(i)),
            )
            for i in range(spec.size)
        ]
        self.queries: list[str] = []

    def search(self, query: str) -> list[dict[str, Any]]:
        """The ``Searcher``: the library's track dicts for at most ten matches."""
        self.queries.append(query)
        years = YEAR_FILTER.search(query)
        words = YEAR_FILTER.sub(" ", query).lower().split()
        out = []
        for t in self.tracks:
            if years and not int(years[1]) <= t.year <= int(years[2] or years[1]):
                continue
            if all(w in f"{t.title} {t.artist} {t.group}".lower() for w in words):
                out.append(t.hit())
        return out[:10]

    def by_id(self) -> dict[str, CatalogueTrack]:
        return {t.spotify_id: t for t in self.tracks}


def analysis_map(tracks: Sequence[CatalogueTrack]) -> dict[str, Any]:
    """The MOWL analysis of a playlist of ``tracks`` in play order (the library's format)."""
    rows: list[dict[str, Any]] = []
    sections: dict[str, list[dict[str, float]]] = {}
    for t in tracks:
        rows.append(
            {
                "Title": t.title,
                "Artist": t.artist,
                "Duration": t.sec * 1000,
                "BPM": t.bpm,
                "SpotifyTrackID": t.spotify_id,
            }
        )
        sections[t.spotify_id] = [
            {
                "start": float(s),
                "duration": float(min(SECTION_SEC, t.sec - s)),
                "loudness": LOUDNESS[n % len(LOUDNESS)],
            }
            for n, s in enumerate(range(0, t.sec, SECTION_SEC))
        ]
    return spotify.build_map(rows, sections)


def kept_songs(spec: ReplaceSpec) -> list[KeptSong]:
    """The seed list's songs that stay: invented, in their slots, with their roles."""
    return [
        KeptSong(
            slot,
            KEPT_ID.format(slot),
            f"Kept Song {slot}",
            f"Kept Artist {slot % 3}",
            role,
            float(FIRST_BPM + BPM_STEP * slot),
            TRACK_SEC,
        )
        for slot, role in enumerate(spec.seed_roles)
        if slot not in spec.replace
    ]
