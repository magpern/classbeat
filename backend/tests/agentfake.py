"""Helpers for the agent tests (ADR 0006): synthetic music, an in-memory stage host, scripts.

The music is invented (no real Spotify data): MOWL-shaped tracks, availability and sections,
returned by an in-process fake of the three ``mowl.api`` reads ``library.spotify_analysis`` makes,
so the analysis goes through the library's real code. ``Planted`` puts a recognisable value in
every place the model must never see (ADR 0006 section 7).
"""

import copy
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mowl import api, library
from mowl.credentials import Credentials

from classbeat.agent.loop import AgentConfig, Counters, RunInfo
from classbeat.agent.model_input import MusicData
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.agent.schemas import Brief
from classbeat.jobs import JobFailure
from classbeat.jobs.run import JobRun
from classbeat.models.jobs import GroupModel

SCRIPTS = Path(__file__).resolve().parent / "agent_scripts"


@dataclass(frozen=True)
class Planted:
    """A value per forbidden place; each is searched for in everything sent to the model."""

    token: str = "a1b2c3d4-planted-mowl-token-000000"
    email: str = "planted.rider@example.test"
    user_id: str = str(uuid.UUID("11111111-2222-4333-8444-555555555555"))
    connection_id: str = str(uuid.UUID("66666666-7777-4888-9999-000000000000"))
    playlist_id: int = 987654321
    spotify_playlist_id: str = "PlantedSpotifyList0042"
    track_id_prefix: str = "PlantedTrk"
    mowl_track_id: int = 55501234
    url: str = "https://open.spotify.com/playlist/PlantedSpotifyList0042"
    unavailable: str = "Planted unavailable text: not playable in SE"
    provider_error: str = "planted provider error text 7d1c"

    def values(self) -> list[str]:
        return [
            self.token,
            self.email,
            self.user_id,
            self.connection_id,
            str(self.playlist_id),
            self.spotify_playlist_id,
            self.track_id_prefix,
            str(self.mowl_track_id),
            self.url,
            self.unavailable,
            "not playable",
            self.provider_error,
        ]


@dataclass
class FakeLibrary:
    """What the fake MOWL reads answer (no network)."""

    tracks: list[dict[str, Any]]
    sections: dict[str, list[dict[str, Any]]]
    problems: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    tokens_seen: list[str] = field(default_factory=lambda: list[str]())


def synthetic_tracks(
    n: int = 8, planted: Planted | None = None, titles: list[str] | None = None
) -> FakeLibrary:
    planted = planted or Planted()
    tracks, sections = [], {}
    for i in range(n):
        sid = f"{planted.track_id_prefix}{i:012d}"[:22]
        tracks.append(
            {
                "TrackID": planted.mowl_track_id + i,
                "PlaylistID": planted.playlist_id,
                "Title": titles[i] if titles and i < len(titles) else f"Invented Song {i}",
                "Artist": f"Made-up Artist {i % 3}",
                "Duration": 240_000,
                "BPM": 120 + 4 * i,
                "SpotifyTrackID": sid,
                "Url": planted.url,
                "OwnerEmail": planted.email,
            }
        )
        sections[sid] = [
            {"start": 0.0, "duration": 60.0, "loudness": -12.0},
            {"start": 60.0, "duration": 60.0, "loudness": -6.0},  # a drop
            {"start": 120.0, "duration": 60.0, "loudness": -11.0},  # a breakdown
            {"start": 180.0, "duration": 60.0, "loudness": -5.0},  # a drop
        ]
    return FakeLibrary(tracks, sections)


def install(monkeypatch: pytest.MonkeyPatch, lib: FakeLibrary) -> None:
    """The three reads of ``library.spotify_analysis`` answer from ``lib``."""

    def tracks(creds: Credentials, playlist_id: int) -> list[dict[str, Any]]:
        lib.tokens_seen.append(creds.mowl_token)
        return copy.deepcopy(lib.tracks)

    def availability(creds: Credentials, playlist_id: int) -> dict[str, Any]:
        return {"PlaylistErrors": copy.deepcopy(lib.problems)}

    def sections(creds: Credentials, spotify_track_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(lib.sections[spotify_track_id])

    monkeypatch.setattr(api, "playlist_tracks", tracks)
    monkeypatch.setattr(api, "playlist_availability", availability)
    monkeypatch.setattr(api, "spotify_sections", sections)


def analysed(
    monkeypatch: pytest.MonkeyPatch,
    lib: FakeLibrary | None = None,
    planted: Planted | None = None,
    name: str = "Invented Tuesday Mix",
) -> MusicData:
    """The analysis as D1 builds it: ``library.spotify_analysis`` over the fake reads."""
    planted = planted or Planted()
    lib = lib or synthetic_tracks(planted=planted)
    install(monkeypatch, lib)
    creds = Credentials(mowl_token=planted.token)
    unavailable, analysis = library.spotify_analysis(
        creds, planted.playlist_id, planted.spotify_playlist_id, name=name
    )
    assert analysis is not None
    return MusicData(analysis, planted.playlist_id, unavailable)


def brief(**overrides: Any) -> Brief:
    values: dict[str, Any] = {"length_min": 30, "intensity": "moderate", "climbs": 1, "sprints": 2}
    values.update(overrides)
    return Brief.model_validate(values)


def script(name: str) -> FakeScript:
    return FakeScript.load(SCRIPTS / f"{name}.json")


def provider(name: str) -> FakeProvider:
    return FakeProvider(script(name))


def from_dict(data: dict[str, Any]) -> FakeProvider:
    return FakeProvider(FakeScript.model_validate_json(json.dumps(data)))


def good_draft() -> dict[str, Any]:
    return copy.deepcopy(script("good_first_draft").drafts["good"])


CONFIG = AgentConfig(model="fake-model")


@dataclass
class FactoryModels:
    """A ``ModelSource`` (``classbeat.models.jobs``) for the kind tests: a new provider from
    ``factory`` for every stage group and attempt, the agent config unchanged, as the worker's
    ``fake`` bypass answers. ``asked`` keeps each ``(route_key, group)`` asked for."""

    factory: Callable[[], ModelProvider]
    asked: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())

    async def __call__(
        self, run: JobRun, route_key: str, group: str, agent: AgentConfig
    ) -> GroupModel:
        self.asked.append((route_key, group))
        try:
            return GroupModel(self.factory(), agent)
        except Exception:
            raise JobFailure("model_config") from None


@dataclass
class RefusingModels:
    """A ``ModelSource`` whose route is unusable: every group is refused with ``model_config``,
    as ``RouteModels`` does for a route that cannot be used. ``asked`` keeps each
    ``(route_key, group)`` asked for."""

    asked: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())

    async def __call__(
        self, run: JobRun, route_key: str, group: str, agent: AgentConfig
    ) -> GroupModel:
        self.asked.append((route_key, group))
        raise JobFailure("model_config")


@dataclass
class MemoryHost:
    """A ``StageHost`` without a database: it keeps what the worker's host would write."""

    spent_before: int = 0
    checkpoints: int = 0
    stop_at_checkpoint: int | None = None
    stop_with: Callable[[], BaseException] | None = None
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=lambda: dict[uuid.UUID, Any]())
    events: list[tuple[str, str | None]] = field(default_factory=lambda: list[Any]())
    infos: list[RunInfo] = field(default_factory=lambda: list[RunInfo]())

    async def checkpoint(self) -> None:
        self.checkpoints += 1
        if self.stop_at_checkpoint is not None and self.checkpoints >= self.stop_at_checkpoint:
            assert self.stop_with is not None
            raise self.stop_with()

    async def begin(self, info: RunInfo) -> tuple[uuid.UUID, int]:
        run_id = uuid.uuid4()
        self.infos.append(info)
        self.runs[run_id] = {"outcome": None, "records": 0}
        return run_id, self.spent_before

    async def record(self, run_id: uuid.UUID, counters: Counters) -> None:
        assert self.runs[run_id]["outcome"] is None
        self.runs[run_id]["records"] += 1
        self.runs[run_id]["counters"] = copy.copy(counters)

    async def tool_called(self, tool: str, error: str | None) -> None:
        self.events.append((tool, error))

    async def finish(self, run_id: uuid.UUID, counters: Counters, outcome: str) -> None:
        assert self.runs[run_id]["outcome"] is None
        self.runs[run_id]["outcome"] = outcome
        self.runs[run_id]["counters"] = copy.copy(counters)

    def outcomes(self) -> list[str | None]:
        return [r["outcome"] for r in self.runs.values()]
