"""Synthetic playlists for the smoke set: invented titles, BPMs and section loudness in the
analysis format of ``library.spotify_analysis``, with no Spotify data and no network.

``MusicSpec`` describes a playlist (how many tracks, how long, which BPMs, which tracks MOWL has no
BPM or sections for, odd titles); ``build_music`` turns it into the ``MusicData`` the design stage
is given, through the library's own ``spotify.build_map`` in the beta's 9 s crossfade. The ids are
invented placeholders that never reach the model (``classbeat.agent.model_input`` drops them).
"""

from typing import Any, Final, Literal

from mowl import spotify
from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent.model_input import MusicData

SECTION_SEC: Final = 60
# A drop every second section: loudness (dB) of consecutive sections, cycling.
LOUDNESS: Final = (-12.0, -6.0, -11.0, -5.0)
PLAYLIST_ID: Final = 1  # a placeholder; the model never sees it


class MusicSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(default="Invented Smoke Mix", min_length=1, max_length=100)
    tracks: int = Field(ge=1, le=40)
    track_sec: int = Field(default=240, ge=60, le=900)
    bpm: int = Field(default=120, ge=60, le=200)  # the first track's BPM
    bpm_step: int = Field(default=4, ge=0, le=20)  # added per track
    no_bpm: list[int] = Field(default_factory=lambda: list[int]())  # MOWL has no BPM yet
    sections: Literal["standard", "none"] = "standard"
    titles: dict[str, str] = Field(default_factory=lambda: dict[str, str]())  # index -> title
    artists: dict[str, str] = Field(default_factory=lambda: dict[str, str]())


def _sections(track_sec: int) -> list[dict[str, float]]:
    return [
        {
            "start": float(start),
            "duration": float(min(SECTION_SEC, track_sec - start)),
            "loudness": LOUDNESS[n % len(LOUDNESS)],
        }
        for n, start in enumerate(range(0, track_sec, SECTION_SEC))
    ]


def build_music(spec: MusicSpec) -> MusicData:
    """The analysed playlist: tracks in play order, fitted with the 9 s crossfade."""
    tracks: list[dict[str, Any]] = []
    sections: dict[str, list[dict[str, float]]] = {}
    for i in range(spec.tracks):
        spotify_id = f"SmokeSynth{i:012d}"
        tracks.append(
            {
                "Title": spec.titles.get(str(i), f"Smoke Song {i}"),
                "Artist": spec.artists.get(str(i), f"Smoke Artist {i % 3}"),
                "Duration": spec.track_sec * 1000,
                "BPM": 0 if i in spec.no_bpm else spec.bpm + spec.bpm_step * i,
                "SpotifyTrackID": spotify_id,
            }
        )
        sections[spotify_id] = _sections(spec.track_sec) if spec.sections == "standard" else []
    analysis = {
        "source": "spotify",
        "source_id": "SmokeSynthPlaylist01",
        "name": spec.name,
        "map": spotify.build_map(tracks, sections),
    }
    return MusicData(analysis, PLAYLIST_ID)
