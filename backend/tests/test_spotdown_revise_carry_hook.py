"""SD-P6 stage E (#1303): the swallows of the revise and refit carry, call by call, with no
database and no network (a stand-in run and fenced session; stage D's ``World`` on the fake
spotdown for the music).

- ``design.spotdown_base.base_sections``: an error of any type in each call (the config, the
  allowlist, the fence, the query, its result, the parse) is ``None`` (today's path), only the type
  logged; ``RunStopped``, ``LeaseLost`` and ``DatabaseRoleError`` pass;
- ``songs.spotdown_carry.carried_music``: an error of any type in each call (the fence, the job's
  class, the list's load, the map, ``spotdown_music``) is exactly today's step, byte for byte; the
  three pass; ``only`` limits the reads to the base's tracks;
- ``design.kind_music_step.music_step``: closes the step when the carry raises; never carries on a
  mix;
- ``songs.pick_analyse.kept_timing``: an error of any type in each call carries nothing; the
  three pass.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from classbeat.agent import ChosenList
from classbeat.agent import track_timing_calc as tt
from classbeat.agent.model_input import MusicData
from classbeat.design import corrections, kind_music_step, spotdown_base
from classbeat.design.music import MusicStep
from classbeat.design.music_choice import SpotifyMusicChoice, SwedebeatMusicChoice
from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.songs import pick_analyse, spotdown_carry
from classbeat.songs.seed import Seeded
from classbeat.spotdown import song_match_config
from classbeat.spotdown.song_analysis_record_model import KEY
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_revise_carry_calc import chosen, entry, record, seeded
from test_spotdown_song_sections_calc import playlist
from test_spotdown_song_sections_hook import ERRORS, OWNER, PASSED, World, switch
from test_spotdown_song_sections_hook import Run as HookRun

__all__ = ["spotdown"]  # the fixture

LIST = uuid4()
FIELD = {"song_list_id": str(LIST), "tracks": [0, 2]}


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class Db:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.asked: list[Any] = []

    async def execute(self, statement: Any, params: Any) -> Result:
        self.asked.append(params)
        return Result(self.value)


class Run(HookRun):
    """A run whose fenced session answers ``value`` to any query."""

    def __init__(self, value: Any = None) -> None:
        super().__init__()
        self.db = Db(value)

    @asynccontextmanager
    async def fenced(self) -> AsyncIterator[Any]:
        yield SimpleNamespace(db=self.db, lease=self.lease)


def raiser(error: BaseException | type[BaseException]) -> Callable[..., Any]:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise error

    return boom


def araiser(error: BaseException | type[BaseException]) -> Callable[..., Any]:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise error

    return boom


@pytest.fixture
def on(spotdown: FakeSpotdown) -> Any:  # the fake installs spotdown
    switch()
    try:
        yield
    finally:
        song_match_config.configure(None)


def failed(caplog: pytest.LogCaptureFixture, message: str) -> list[str]:
    return [getattr(r, "error_type", "") for r in caplog.records if r.getMessage() == message]


# --- base_sections --------------------------------------------------------------------------------


@pytest.mark.usefixtures("on")
async def test_the_base_field_is_read_through_the_jobs_class() -> None:
    run = Run(FIELD)
    got = await spotdown_base.base_sections(run, LIST)  # pyright: ignore[reportArgumentType]
    assert got == SpotdownSections(song_list_id=LIST, tracks=[0, 2])
    [params] = run.db.asked
    assert params == {"base": LIST, "job_id": run.lease.job_id, "owner_id": OWNER}


@pytest.mark.usefixtures("on")
@pytest.mark.parametrize("value", [None, {"tracks": [0]}, {**FIELD, "tracks": [2, 0]}, "x", 3])
async def test_no_or_a_bad_field_is_todays_path(value: Any) -> None:
    assert await spotdown_base.base_sections(Run(value), LIST) is None  # pyright: ignore[reportArgumentType]


@pytest.mark.usefixtures("spotdown")
async def test_off_or_not_listed_reads_nothing() -> None:
    run = Run(FIELD)
    assert await spotdown_base.base_sections(run, LIST) is None  # pyright: ignore[reportArgumentType]
    switch(frozenset({uuid4()}))
    try:
        assert await spotdown_base.base_sections(run, LIST) is None  # pyright: ignore[reportArgumentType]
    finally:
        song_match_config.configure(None)
    assert run.db.asked == []


def _base_targets(
    monkeypatch: pytest.MonkeyPatch, run: Run
) -> dict[str, Callable[[BaseException | type[BaseException]], None]]:
    return {
        "config": lambda e: monkeypatch.setattr(song_match_config, "current", raiser(e)),
        "active_for": lambda e: monkeypatch.setattr(
            song_match_config.MatchConfig, "active_for", raiser(e)
        ),
        "fenced": lambda e: setattr(run, "fenced", raiser(e)),
        "execute": lambda e: setattr(run.db, "execute", araiser(e)),
        "result": lambda e: monkeypatch.setattr(Result, "scalar_one_or_none", raiser(e)),
        "parse": lambda e: monkeypatch.setattr(
            spotdown_base.SpotdownSections, "model_validate_json", raiser(e)
        ),
    }


BASE_TARGETS = ("config", "active_for", "fenced", "execute", "result", "parse")


@pytest.mark.usefixtures("on")
@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("target", BASE_TARGETS)
async def test_an_error_of_any_type_in_each_base_call_is_todays_path(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    target: str,
    error: type[Exception],
) -> None:
    run = Run(FIELD)
    _base_targets(monkeypatch, run)[target](error("injected secret"))
    with caplog.at_level(logging.INFO):
        assert await spotdown_base.base_sections(run, LIST) is None  # pyright: ignore[reportArgumentType]
    assert failed(caplog, "spotdown base sections failed") == [error.__name__]
    assert "secret" not in " ".join(str(r.__dict__) for r in caplog.records)


@pytest.mark.usefixtures("on")
@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("target", BASE_TARGETS)
async def test_a_stop_a_lost_lease_and_a_role_error_pass_the_base_read(
    monkeypatch: pytest.MonkeyPatch, target: str, stop: Exception
) -> None:
    run = Run(FIELD)
    _base_targets(monkeypatch, run)[target](stop)
    with pytest.raises(type(stop)):
        await spotdown_base.base_sections(run, LIST)  # pyright: ignore[reportArgumentType]


# --- carried_music --------------------------------------------------------------------------------


class Carry:
    """``carried_music`` over stage D's ``World``: its list is what ``song_list.load`` gives."""

    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world, self.monkeypatch = world, monkeypatch
        self.loaded: list[Any] = []

        async def load(*args: Any) -> Any:
            self.loaded.append(args)
            return world.stored

        async def class_of(_fs: Any) -> Any:
            return "class"

        monkeypatch.setattr(spotdown_carry.song_list, "load", load)
        monkeypatch.setattr(spotdown_carry, "class_of", class_of)

    async def carry(self, run: Run, tracks: list[int]) -> MusicStep:
        step = self.world.step()
        sections = SpotdownSections(song_list_id=LIST, tracks=tracks)
        out = await spotdown_carry.carried_music(run, step, sections)  # pyright: ignore[reportArgumentType]
        assert out is step
        return out


@pytest.fixture
def carry(spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch) -> Any:
    switch()
    try:
        yield Carry(World(spotdown), monkeypatch)
    finally:
        song_match_config.configure(None)


async def test_only_the_bases_tracks_are_read_and_stamped(carry: Carry) -> None:
    step = await carry.carry(Run(), [0, 2])
    assert tt.classes(step.data.analysis["map"]) == {0: "approximate", 2: "approximate"}
    ids = carry.world.ids
    assert [s.path for s in carry.world.reads()] == [
        f"/v2/analyses/{ids[0]}", f"/v2/analyses/{ids[2]}",
    ]  # fmt: skip
    [(_db, owner, cls, list_id)] = carry.loaded
    assert (owner, cls, list_id) == (OWNER, "class", LIST)


async def test_other_music_than_the_lists_is_today_and_reads_nothing(carry: Carry) -> None:
    carry.world.stored.track_ids = tuple(reversed(carry.world.stored.track_ids))
    step = await carry.carry(Run(), [0, 2])
    assert carry.world.is_today(step)
    assert carry.world.reads() == []


async def test_a_list_that_does_not_load_is_today(
    carry: Carry, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none(*_a: Any) -> Any:
        return None

    monkeypatch.setattr(spotdown_carry.song_list, "load", none)
    assert carry.world.is_today(await carry.carry(Run(), [0]))
    assert carry.world.reads() == []


class BadMap(dict[str, Any]):
    def __init__(self, error: BaseException, base: dict[str, Any]) -> None:
        super().__init__(base)
        self.error = error

    def __getitem__(self, key: str) -> Any:
        raise self.error


def _carry_targets(
    monkeypatch: pytest.MonkeyPatch, run: Run, world: World
) -> dict[str, Callable[[BaseException | type[BaseException]], None]]:
    def bad_map(e: BaseException | type[BaseException]) -> None:
        error = e if isinstance(e, BaseException) else e("injected")
        analysis = {**world.analysis, "map": BadMap(error, world.analysis["map"])}
        world.data = MusicData(analysis, 7, ("gone",))  # ``unchanged`` compares the object

    return {
        "fenced": lambda e: setattr(run, "fenced", raiser(e)),
        "class_of": lambda e: monkeypatch.setattr(spotdown_carry, "class_of", araiser(e)),
        "load": lambda e: monkeypatch.setattr(spotdown_carry.song_list, "load", araiser(e)),
        "map": bad_map,
        "spotdown_music": lambda e: monkeypatch.setattr(
            spotdown_carry, "spotdown_music", araiser(e)
        ),
    }


CARRY_TARGETS = ("fenced", "class_of", "load", "map", "spotdown_music")


def unchanged(world: World, step: MusicStep) -> bool:
    if isinstance(step.data.analysis["map"], BadMap):
        return step.data is world.data
    return world.is_today(step)


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("target", CARRY_TARGETS)
async def test_an_error_of_any_type_in_each_carry_call_is_today(
    carry: Carry,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    target: str,
    error: type[Exception],
) -> None:
    run = Run()
    _carry_targets(monkeypatch, run, carry.world)[target](error("injected vid00000001 secret"))
    with caplog.at_level(logging.INFO):
        step = await carry.carry(run, [0, 2])
    assert unchanged(carry.world, step)
    assert tt.classes(dict(step.data.analysis["map"])) == {}
    assert failed(caplog, "spotdown carried sections failed") == [error.__name__]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for leak in ("vid0000", "secret"):
        assert leak not in text


@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("target", CARRY_TARGETS)
async def test_a_stop_a_lost_lease_and_a_role_error_pass_the_carry(
    carry: Carry, monkeypatch: pytest.MonkeyPatch, target: str, stop: Exception
) -> None:
    run = Run()
    _carry_targets(monkeypatch, run, carry.world)[target](stop)
    with pytest.raises(type(stop)):
        await carry.carry(run, [0, 2])


# --- music_step -----------------------------------------------------------------------------------


def _opened_step() -> MusicStep:
    opened = SimpleNamespace(id=uuid4(), generation=3)
    return MusicStep(MusicData({"map": playlist()}, 1), "P", uuid4(), "fp", (), opened)  # pyright: ignore[reportArgumentType]


SECTIONS = SpotdownSections(song_list_id=LIST, tracks=[0])
PLAYLIST = SpotifyMusicChoice(source="mowl_spotify_playlist", spotify_playlist_id="a" * 22)


@pytest.mark.parametrize("stop", [*PASSED, ValueError("x")], ids=lambda e: type(e).__name__)
async def test_music_step_closes_the_step_when_the_carry_raises(
    monkeypatch: pytest.MonkeyPatch, stop: Exception
) -> None:
    step = _opened_step()

    async def load(*_a: Any, **_k: Any) -> MusicStep:
        return step

    monkeypatch.setattr(kind_music_step, "load_music", load)
    monkeypatch.setattr(spotdown_carry, "carried_music", araiser(stop))
    runtime = SimpleNamespace(keyring=None)
    with pytest.raises(type(stop)):
        await kind_music_step.music_step(None, runtime, PLAYLIST, uuid4(), None, SECTIONS)  # pyright: ignore[reportArgumentType]
    assert step.connection_id is None  # closed


async def test_music_step_carries_a_playlist_before_the_corrections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step, order = _opened_step(), []

    async def load(*_a: Any, **_k: Any) -> MusicStep:
        return step

    async def carried(_run: Any, s: MusicStep, sections: Any) -> MusicStep:
        order.append(("carry", sections))
        return s

    async def attach(_run: Any, s: MusicStep) -> MusicStep:
        order.append(("corrections", None))
        return s

    monkeypatch.setattr(kind_music_step, "load_music", load)
    monkeypatch.setattr(spotdown_carry, "carried_music", carried)
    monkeypatch.setattr(corrections, "attach", attach)
    runtime = SimpleNamespace(keyring=None)
    out = await kind_music_step.music_step(None, runtime, PLAYLIST, uuid4(), None, SECTIONS)  # pyright: ignore[reportArgumentType]
    assert out is step
    assert order == [("carry", SECTIONS), ("corrections", None)]
    order.clear()
    await kind_music_step.music_step(None, runtime, PLAYLIST, uuid4(), None, None)  # pyright: ignore[reportArgumentType]
    assert order == [("corrections", None)]  # no carry: today's step


async def test_music_step_never_carries_on_a_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    step = _opened_step()

    async def mix(*_a: Any, **_k: Any) -> MusicStep:
        return step

    async def attach(_run: Any, s: MusicStep) -> MusicStep:
        return s

    monkeypatch.setattr(kind_music_step, "swedebeat_step", mix)
    monkeypatch.setattr(spotdown_carry, "carried_music", raiser(AssertionError("carried")))
    monkeypatch.setattr(corrections, "attach", attach)
    music = SwedebeatMusicChoice(source="mowl_swedebeat_mix", mix_id="mix1")
    runtime = SimpleNamespace(keyring=None)
    out = await kind_music_step.music_step(None, runtime, music, uuid4(), None, SECTIONS)  # pyright: ignore[reportArgumentType]
    assert out is step


# --- kept_timing ----------------------------------------------------------------------------------


def _kept_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Callable[[BaseException | type[BaseException]], Any]]:
    """Each call ``kept_timing`` makes: the kept songs, the chosen songs, ``with_kept``."""

    class Raising:
        def __init__(self, error: BaseException | type[BaseException]) -> None:
            self.error = error

        def __getattr__(self, name: str) -> Any:
            raise self.error

    def kept(e: BaseException | type[BaseException]) -> Any:
        s = seeded()
        bad = Seeded(s.revision_id, (Raising(e),), s.roles, s.provenance)  # pyright: ignore[reportArgumentType]
        return pick_analyse.kept_timing(record(entry(2)), bad, chosen())

    def listed(e: BaseException | type[BaseException]) -> Any:
        bad = ChosenList((Raising(e),), 1000, 1000, 0, "fill", 0, "")  # pyright: ignore[reportArgumentType]
        return pick_analyse.kept_timing(record(entry(2)), seeded(), bad)

    def calc(e: BaseException | type[BaseException]) -> Any:
        monkeypatch.setattr(pick_analyse, "with_kept", raiser(e))
        return pick_analyse.kept_timing(record(entry(2)), seeded(), chosen())

    return {"kept": kept, "listed": listed, "with_kept": calc}


KEPT_TARGETS = ("kept", "listed", "with_kept")


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("target", KEPT_TARGETS)
def test_an_error_of_any_type_in_each_kept_call_carries_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    target: str,
    error: type[Exception],
) -> None:
    with caplog.at_level(logging.INFO):
        out = _kept_targets(monkeypatch)[target](error("injected"))
    assert out == record(entry(2))
    assert [e["position"] for e in out[KEY]["songs"]] == [2]
    assert failed(caplog, "spotdown kept timing failed") == [error.__name__]


@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("target", KEPT_TARGETS)
def test_a_stop_a_lost_lease_and_a_role_error_pass_the_kept_carry(
    monkeypatch: pytest.MonkeyPatch, target: str, stop: Exception
) -> None:
    with pytest.raises(type(stop)):
        _kept_targets(monkeypatch)[target](stop)
