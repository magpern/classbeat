"""SD-P6 stage D (#1303, ADR 0040 sections 2, 3 and 8; #1354): the phase-2 hook
(``songs.phase2_spotdown.spotdown_music``) on the fake spotdown (``spotdownfake``), with no
database and no network: a stand-in run and a real ``MusicStep`` on MOWL's map of four invented
tracks.

- every song of the list is on spotdown's sections and stamped ``approximate``; one ``GET`` per
  song, global (no ``cache_scope``), nothing else asked;
- a list without the record (an own-playlist or composer list, which run no analysis stage) is
  today's music, nothing is asked;
- there is no MOWL-sections fallback: a read that finds spotdown unreachable or ``rate_limited``
  is ``analysis_unavailable``; a recorded analysis that is gone, does not verify, does not map, is
  not ``approximate``, or a song with no recorded entry, is ``analysis_failed``; a track-id
  mismatch is ``internal`` (``outage_policy``);
- an error of any type in each call the hook makes propagates (it is not swallowed): the caller
  maps it to ``internal`` and ``phase2.final_music`` closes the step.
"""

import copy
import json
import logging
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from classbeat.agent import track_timing_calc as tt
from classbeat.agent.model_input import MusicData
from classbeat.db import DatabaseRoleError
from classbeat.design.music import MusicStep
from classbeat.jobs import JobFailure, RunStopped
from classbeat.jobs.errors import LeaseLost
from classbeat.songs import phase2, phase2_spotdown
from classbeat.spotdown import outage_policy, song_match_config
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.provenance_read_calc import AudioIdentity
from classbeat.spotdown.song_analysis_record_model import (
    Analysed,
    SongOutcome,
    analysis_record,
)
from classbeat.spotdown.song_sections_calc import reference_id
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.track_fit_policy import Fit
from songanalysisdocs import youtube_doc
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_song_sections_calc import IDS, SEC, playlist

__all__ = ["spotdown"]  # the fixture

OWNER = uuid4()
ERRORS = (OSError, ValueError, KeyError, TypeError, ZeroDivisionError, AttributeError,
          LookupError, PermissionError)  # fmt: skip
PASSED = (RunStopped("cancelled"), LeaseLost(), DatabaseRoleError("role"))


def vid(n: int) -> str:
    return f"vid{n:08d}"


def sha(n: int) -> str:
    return f"{n + 1:064x}"


class Run:
    def __init__(self) -> None:
        self.lease = SimpleNamespace(owner_id=OWNER, job_id=uuid4())
        self.checkpoints = 0

    async def checkpoint(self) -> None:
        self.checkpoints += 1


def record(positions: tuple[int, ...], ids: dict[int, str], cls: str = "approximate") -> Any:
    outcomes = {}
    for p in positions:
        decision = TimingDecision(cls, "matched_recording", {"residual_s": 0.3})  # pyright: ignore[reportArgumentType]
        audio = AudioIdentity(sha(p), "fp", "mp3", 44100.0, 2.0, 240.0, 240.0)
        analysed = Analysed(ids[p], ("spotdown", "1", None), audio, decision)
        outcomes[p] = SongOutcome("spotdown", analysed, Fit(True, ()))
    return analysis_record(outcomes, {}, rounds=0)


class World:
    def __init__(self, fake: FakeSpotdown, positions: tuple[int, ...] = (0, 1, 2, 3)) -> None:
        self.fake = fake
        self.ids: dict[int, str] = {}
        for n in range(4):
            source = f"youtube:{vid(n)}"
            doc = youtube_doc(vid(n), 124.0)
            doc["provenance"]["audio"]["sha256"] = sha(n)
            reference = {"kind": "spotify", "id": reference_id(IDS[n]), "duration_s": float(SEC)}
            doc["alignment"]["reference"] = reference
            fake.documents[source] = doc
            self.ids[n] = fake.add_analysis(None, source)
        self.stored = SimpleNamespace(provenance=record(positions, self.ids), track_ids=tuple(IDS))
        self.analysis = {"source": "spotify", "source_id": "P", "name": "N", "map": playlist()}
        self.data = MusicData(self.analysis, 7, ("gone",))
        self.today = json.dumps(self.analysis, sort_keys=True)

    def step(self) -> MusicStep:
        return MusicStep(self.data, "P", uuid4(), "fp", (), None)

    async def hook(self, run: Run | None = None) -> MusicStep:
        step = self.step()
        out = await phase2_spotdown.spotdown_music(run or Run(), self.stored, step)  # pyright: ignore[reportArgumentType]
        assert out is step
        return out

    async def fails(self, category: str) -> None:
        """The hook ends ``JobFailure(category)`` and leaves the music as it was."""
        with pytest.raises(JobFailure) as raised:
            await self.hook()
        assert raised.value.category == category
        assert self.is_today(self.step())

    def is_today(self, step: MusicStep) -> bool:
        """Exactly today's music: the same object, unchanged, byte for byte."""
        return step.data is self.data and json.dumps(step.data.analysis, sort_keys=True) == (
            self.today
        )

    def reads(self) -> list[Any]:
        return [s for s in self.fake.seen if s.path.startswith("/v2/analyses/")]


@pytest.fixture
def world(spotdown: FakeSpotdown) -> Iterator[World]:
    song_match_config.configure(song_match_config.MatchConfig(frozenset({"youtube"})))
    try:
        yield World(spotdown)
    finally:
        song_match_config.configure(None)


def track_songs(m: dict[str, Any], track: int) -> list[dict[str, Any]]:
    return [{k: v for k, v in s.items() if k != "song"} for s in m["songs"] if s["track"] == track]


# --- on -------------------------------------------------------------------------------------------


async def test_every_marked_song_is_on_spotdowns_sections_and_approximate(world: World) -> None:
    run = Run()
    step = await world.hook(run)
    m = step.data.analysis["map"]
    assert tt.classes(m) == {n: "approximate" for n in range(4)}
    assert [(t["timing"], t["timing_source"]) for t in m["tracks"]] == [
        ("approximate", "youtube")
    ] * 4
    before = world.analysis["map"]
    assert [{k: v for k, v in t.items() if not k.startswith("timing")} for t in m["tracks"]] == (
        before["tracks"]
    )  # the same tracks, in order, with MOWL's length and BPM
    assert m["duration"] == before["duration"]
    assert step.data.playlist_id == 7
    assert step.data.unavailable == ("gone",)
    assert world.analysis["map"] == playlist()  # today's map itself is never changed
    # spotdown's four 60 s sections of each track (youtube_doc), on the Spotify clock
    for n in range(4):
        starts = [s["start"] for s in m["songs"] if s["track"] == n + 1]
        assert [round(s - starts[0], 3) for s in starts] == [0.0, 60.0, 120.0, 180.0]
    reads = world.reads()
    assert [r.path for r in reads] == [f"/v2/analyses/{world.ids[n]}" for n in range(4)]
    assert all(r.method == "GET" and "cache_scope" not in r.query for r in reads)
    assert len(world.fake.seen) == 4  # nothing else asked
    assert run.checkpoints == 4


async def test_a_song_with_no_recorded_entry_is_analysis_failed(spotdown: FakeSpotdown) -> None:
    """A designed track always has a stamp: a position the record has no spotdown entry for ends
    the job; nothing is made of MOWL's sections for it."""
    song_match_config.configure(song_match_config.MatchConfig(frozenset({"youtube"})))
    try:
        world = World(spotdown, positions=(1, 3))
        await world.fails("analysis_failed")
    finally:
        song_match_config.configure(None)
    assert [r.path for r in world.reads()] == []  # stopped at the first (position 0)


# --- a list without the record: the own-playlist exemption ------------------------------------


async def test_a_list_without_the_record_asks_nothing_and_is_today(world: World) -> None:
    world.stored.provenance = {}
    assert world.is_today(await world.hook())
    assert world.fake.seen == []


# --- spotdown cannot be used: no fallback --------------------------------------------------------


async def test_unreachable_is_analysis_unavailable_and_stops_at_the_first_read(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.fake.connect_error = True
    with caplog.at_level(logging.INFO):
        await world.fails("analysis_unavailable")
    assert len(world.reads()) == 1  # the first read; nothing after it
    assert outage_policy.log_message("connection") in caplog.text  # the reason is logged


# (status of the read of song 2, the category the job ends with)
READS = [
    (500, "analysis_unavailable"),
    (503, "analysis_unavailable"),
    (401, "analysis_unavailable"),
    (404, "analysis_failed"),  # the recorded analysis is gone
    (422, "analysis_failed"),
    (409, "internal"),
]


@pytest.mark.parametrize(("status", "category"), READS)
async def test_a_failed_read_of_any_song_ends_the_job_in_its_category(
    world: World, status: int, category: str
) -> None:
    world.fake.inject[f"/v2/analyses/{world.ids[2]}"] = [httpx.Response(status)]
    await world.fails(category)
    assert len(world.reads()) == 3  # stopped at the failed one


async def test_a_rate_limited_read_is_analysis_unavailable_without_a_wait(world: World) -> None:
    world.fake.inject[f"/v2/analyses/{world.ids[2]}"] = [
        httpx.Response(429, headers={"Retry-After": "30"})
    ]
    await world.fails("analysis_unavailable")
    assert len(world.reads()) == 3  # no wait, no second read


@pytest.mark.parametrize("case", ["reference", "sha", "kind", "no_alignment", "too_long"])
async def test_a_document_not_verified_is_analysis_failed(world: World, case: str) -> None:
    doc = world.fake.documents[f"youtube:{vid(2)}"]
    if case == "reference":
        doc["alignment"]["reference"]["id"] = reference_id(IDS[1])  # aligned to another track
    elif case == "sha":
        doc["provenance"]["audio"]["sha256"] = sha(9)
    elif case == "kind":
        doc["provenance"]["source"]["kind"] = "url"
    elif case == "no_alignment":
        doc.pop("alignment")
    else:
        doc["alignment"]["reference"]["duration_s"] = SEC + 5.0
    await world.fails("analysis_failed")


async def test_a_document_that_does_not_map_is_analysis_failed(world: World) -> None:
    doc = world.fake.documents[f"youtube:{vid(2)}"]
    doc["tempo"]["bpm"] = None  # no trusted BPM: ``reference_sections`` refuses it
    await world.fails("analysis_failed")


@pytest.mark.parametrize("cls", ["unusable", "exact"])
async def test_a_recorded_class_that_is_not_approximate_is_analysis_failed(
    world: World, cls: str
) -> None:
    world.stored.provenance = record((0, 1, 2, 3), world.ids, cls)
    await world.fails("analysis_failed")


async def test_an_unknown_class_after_the_read_is_analysis_failed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = phase2_spotdown.recorded

    def odd(provenance: Any) -> Any:
        got = real(provenance)
        weird = TimingDecision("precise", "r", {})  # pyright: ignore[reportArgumentType]
        return [type(r)(r.position, r.analysis_id, r.sha256, weird, r.source_kind)
                if r.position == 2 else r for r in got]  # fmt: skip

    monkeypatch.setattr(phase2_spotdown, "recorded", odd)
    await world.fails("analysis_failed")


async def test_a_track_id_that_is_not_the_lists_is_internal(world: World) -> None:
    world.stored.track_ids = (IDS[1], IDS[0], IDS[2], IDS[3])  # order is checked before: a bug
    await world.fails("internal")


# --- an error of any type in each call ------------------------------------------------------------


def _targets(monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable[[Callable[..., Any]], None]]:
    def module(name: str) -> Callable[[Callable[..., Any]], None]:
        return lambda f: monkeypatch.setattr(phase2_spotdown, name, f)

    return {
        "config": lambda f: monkeypatch.setattr(song_match_config, "current", f),
        "recorded": module("recorded"),
        "client": module("SpotdownClient"),
        "get_analysis": lambda f: monkeypatch.setattr(SpotdownClient, "get_analysis", f),
        "verified": module("verified"),
        "reference_sections": module("reference_sections"),
        "source_kind": module("source_kind"),
        "apply_track": module("apply_track"),
    }


TARGETS = ("config", "recorded", "client", "get_analysis", "verified", "reference_sections",
           "source_kind", "apply_track", "checkpoint")  # fmt: skip


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("target", TARGETS)
async def test_an_error_of_any_type_in_each_call_propagates_and_leaks_nothing(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    target: str,
    error: type[Exception],
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise error("injected vid00000001 SectSynth secret")

    async def aboom(*_a: Any, **_k: Any) -> Any:
        raise error("injected vid00000001 SectSynth secret")

    run = Run()
    if target == "checkpoint":
        run.checkpoint = aboom  # type: ignore[method-assign]
    elif target == "get_analysis":
        _targets(monkeypatch)[target](aboom)
    else:
        _targets(monkeypatch)[target](boom)
    with caplog.at_level(logging.INFO), pytest.raises(error):
        await world.hook(run)  # the caller (the job runner) ends the attempt ``internal``
    assert world.is_today(world.step())
    ours = [r for r in caplog.records if r.name.startswith("classbeat")]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in ours)
    for leak in ("vid0000", "SectSynth", "secret", "an_0"):
        assert leak not in text


@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("where", ["checkpoint", "get_analysis"])
async def test_a_stop_a_lost_lease_and_a_role_error_pass(
    world: World, monkeypatch: pytest.MonkeyPatch, stop: Exception, where: str
) -> None:
    async def raising(*_a: Any, **_k: Any) -> Any:
        raise stop

    run = Run()
    if where == "checkpoint":
        run.checkpoint = raising  # type: ignore[method-assign]
    else:
        monkeypatch.setattr(SpotdownClient, "get_analysis", raising)
    with pytest.raises(type(stop)):
        await phase2_spotdown.spotdown_music(run, world.stored, world.step())  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("stop", [*PASSED, ValueError("x")], ids=lambda e: type(e).__name__)
async def test_final_music_closes_the_step_when_the_hook_raises(
    monkeypatch: pytest.MonkeyPatch, stop: Exception
) -> None:
    opened = SimpleNamespace(id=uuid4(), generation=3)
    step = MusicStep(MusicData({"map": playlist()}, 1), "P", uuid4(), "fp", (), opened)  # pyright: ignore[reportArgumentType]
    assert step.generation == 3  # open until the hook raises
    p = SimpleNamespace(stored=SimpleNamespace(provenance={}, track_ids=()))

    async def prepare(*_a: Any, **_k: Any) -> Any:
        return p

    async def build(*_a: Any, **_k: Any) -> Any:
        return "final"

    async def music(*_a: Any, **_k: Any) -> MusicStep:
        return step

    async def hook(*_a: Any, **_k: Any) -> MusicStep:
        raise stop

    monkeypatch.setattr(phase2, "prepare", prepare)
    monkeypatch.setattr(phase2, "build_playlist", build)
    monkeypatch.setattr(phase2, "_music", music)
    monkeypatch.setattr(phase2, "spotdown_music", hook)
    with pytest.raises(type(stop)):
        await phase2.final_music(None, None, None, None)  # pyright: ignore[reportArgumentType]
    assert step.connection_id is None  # closed
    assert step.generation is None


async def test_final_music_hands_the_hook_the_stored_list_and_the_checked_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = MusicStep(MusicData({"map": playlist()}, 1), "P", uuid4(), "fp", (), None)
    stored = SimpleNamespace(provenance={}, track_ids=())
    seen: list[Any] = []

    async def prepare(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(stored=stored)

    async def build(*_a: Any, **_k: Any) -> Any:
        return "final"

    async def music(*_a: Any, **_k: Any) -> MusicStep:
        return step

    async def hook(run: Any, s: Any, st: MusicStep) -> MusicStep:
        seen.append((run, s, st))
        return copy.copy(st)

    monkeypatch.setattr(phase2, "prepare", prepare)
    monkeypatch.setattr(phase2, "build_playlist", build)
    monkeypatch.setattr(phase2, "_music", music)
    monkeypatch.setattr(phase2, "spotdown_music", hook)
    _, final, out = await phase2.final_music("run", None, None, None)  # pyright: ignore[reportArgumentType]
    assert seen == [("run", stored, step)]
    assert final == "final"
    assert out is not step
