"""SD-P6 stage E (#1303, #1354): the revise and refit carry, call by call, with no database and no
network (a stand-in run and fenced session; stage D's ``World`` on the fake spotdown for the music).
Nothing here is swallowed: there is no fallback to MOWL's sections.

- ``design.spotdown_base.base_sections``: a base on a song list with no field is
  ``music_changed`` (#1386), a mix base is ``None`` and reads nothing, a ``design_draft`` base
  (no list) is ``None``, a row or a field that does not parse is ``internal``, an error of any
  type in each call (the fence, the query, its result) propagates;
- ``songs.spotdown_carry.carried_music``: the list gone, other music than the list's, or a base that
  had only some tracks on spotdown is ``music_changed``; every track of the list is read again;
  what ``spotdown_music`` ends in passes; an error of any type in each call propagates;
- ``design.kind_music_step.music_step``: closes the step when the carry raises; never carries on a
  mix;
- ``songs.pick_analyse.kept_timing``: an error of any type in each call propagates.
"""

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
from classbeat.jobs import JobFailure
from classbeat.songs import pick_analyse, spotdown_carry
from classbeat.songs.seed import Seeded
from classbeat.spotdown import song_match_config
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_revise_carry_calc import chosen, entry, record, seeded
from test_spotdown_song_sections_calc import playlist
from test_spotdown_song_sections_hook import ERRORS, OWNER, PASSED, World
from test_spotdown_song_sections_hook import Run as HookRun

__all__ = ["spotdown"]  # the fixture

LIST = uuid4()
FIELD = {"song_list_id": str(LIST), "tracks": [0, 2]}
MUSIC = {"source": "mowl_spotify_playlist", "song_list_id": str(LIST), "spotdown": FIELD}


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
    song_match_config.configure(song_match_config.MatchConfig(frozenset({"youtube"})))
    try:
        yield
    finally:
        song_match_config.configure(None)


# --- base_sections --------------------------------------------------------------------------------


@pytest.mark.usefixtures("on")
async def test_the_base_field_is_read_through_the_jobs_class() -> None:
    run = Run(MUSIC)
    got = await spotdown_base.base_sections(run, LIST, mix=False)  # pyright: ignore[reportArgumentType]
    assert got == SpotdownSections(song_list_id=LIST, tracks=[0, 2])
    [params] = run.db.asked
    assert params == {"base": LIST, "job_id": run.lease.job_id, "owner_id": OWNER}


@pytest.mark.parametrize("spotdown", [None, "absent"])
async def test_a_base_on_a_song_list_without_the_field_is_music_changed(spotdown: Any) -> None:
    """#1386: no "today's music" for a base designed on a list (never MOWL's sections)."""
    music = {k: v for k, v in MUSIC.items() if k != "spotdown"}
    if spotdown is None:
        music["spotdown"] = None
    with pytest.raises(JobFailure) as raised:
        await spotdown_base.base_sections(Run(music), LIST, mix=False)  # pyright: ignore[reportArgumentType]
    assert raised.value.category == "music_changed"


async def test_a_design_draft_base_with_no_song_list_is_none() -> None:
    """A ``design_draft`` on the user's MOWL playlist has no list (outside #1386)."""
    music = {"source": "mowl_spotify_playlist", "link_id": "x", "fingerprint": "f"}
    assert await spotdown_base.base_sections(Run(music), LIST, mix=False) is None  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("value", [None, "x", 3, [MUSIC]])
async def test_a_base_row_that_is_not_music_is_internal(value: Any) -> None:
    with pytest.raises(JobFailure) as raised:
        await spotdown_base.base_sections(Run(value), LIST, mix=False)  # pyright: ignore[reportArgumentType]
    assert raised.value.category == "internal"


@pytest.mark.parametrize("value", [None, MUSIC])
async def test_a_mix_base_is_none_and_reads_nothing(value: Any) -> None:
    run = Run(value)
    assert await spotdown_base.base_sections(run, LIST, mix=True) is None  # pyright: ignore[reportArgumentType]
    assert run.db.asked == []  # a mix's own path reads spotdown, never a carry


@pytest.mark.parametrize("value", [{"tracks": [0]}, {**FIELD, "tracks": [2, 0]}, "x", 3])
async def test_a_field_that_does_not_parse_is_internal(value: Any) -> None:
    with pytest.raises(JobFailure) as raised:
        await spotdown_base.base_sections(Run({**MUSIC, "spotdown": value}), LIST, mix=False)  # pyright: ignore[reportArgumentType]
    assert raised.value.category == "internal"


def _base_targets(
    monkeypatch: pytest.MonkeyPatch, run: Run
) -> dict[str, Callable[[BaseException | type[BaseException]], None]]:
    return {
        "fenced": lambda e: setattr(run, "fenced", raiser(e)),
        "execute": lambda e: setattr(run.db, "execute", araiser(e)),
        "result": lambda e: monkeypatch.setattr(Result, "scalar_one_or_none", raiser(e)),
    }


BASE_TARGETS = ("fenced", "execute", "result")


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("target", BASE_TARGETS)
async def test_an_error_of_any_type_in_each_base_call_propagates(
    monkeypatch: pytest.MonkeyPatch, target: str, error: type[Exception]
) -> None:
    run = Run(MUSIC)
    _base_targets(monkeypatch, run)[target](error("injected secret"))
    with pytest.raises(error):
        await spotdown_base.base_sections(run, LIST, mix=False)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("target", BASE_TARGETS)
async def test_a_stop_a_lost_lease_and_a_role_error_pass_the_base_read(
    monkeypatch: pytest.MonkeyPatch, target: str, stop: Exception
) -> None:
    run = Run(MUSIC)
    _base_targets(monkeypatch, run)[target](stop)
    with pytest.raises(type(stop)):
        await spotdown_base.base_sections(run, LIST, mix=False)  # pyright: ignore[reportArgumentType]


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
    song_match_config.configure(song_match_config.MatchConfig(frozenset({"youtube"})))
    try:
        yield Carry(World(spotdown), monkeypatch)
    finally:
        song_match_config.configure(None)


ALL = [0, 1, 2, 3]


async def test_every_track_of_the_list_is_read_and_stamped(carry: Carry) -> None:
    step = await carry.carry(Run(), ALL)
    assert tt.classes(step.data.analysis["map"]) == dict.fromkeys(ALL, "approximate")
    ids = carry.world.ids
    assert [s.path for s in carry.world.reads()] == [f"/v2/analyses/{ids[n]}" for n in ALL]
    [(_db, owner, cls, list_id)] = carry.loaded
    assert (owner, cls, list_id) == (OWNER, "class", LIST)


async def _music_changed(carry: Carry, tracks: list[int]) -> None:
    with pytest.raises(JobFailure) as raised:
        await carry.carry(Run(), tracks)
    assert raised.value.category == "music_changed"
    assert carry.world.reads() == []  # nothing asked of spotdown


async def test_other_music_than_the_lists_is_music_changed(carry: Carry) -> None:
    carry.world.stored.track_ids = tuple(reversed(carry.world.stored.track_ids))
    await _music_changed(carry, ALL)


async def test_a_base_with_only_some_tracks_on_spotdown_is_music_changed(carry: Carry) -> None:
    await _music_changed(carry, [0, 2])


async def test_a_list_that_does_not_load_is_music_changed(
    carry: Carry, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none(*_a: Any) -> Any:
        return None

    monkeypatch.setattr(spotdown_carry.song_list, "load", none)
    await _music_changed(carry, ALL)


@pytest.mark.parametrize(
    ("status", "category"), [(503, "analysis_unavailable"), (404, "analysis_failed")]
)
async def test_what_spotdown_music_ends_in_passes(carry: Carry, status: int, category: str) -> None:
    import httpx

    carry.world.fake.inject[f"/v2/analyses/{carry.world.ids[1]}"] = [httpx.Response(status)]
    with pytest.raises(JobFailure) as raised:
        await carry.carry(Run(), ALL)
    assert raised.value.category == category
    assert carry.world.is_today(carry.world.step())


def _carry_targets(
    monkeypatch: pytest.MonkeyPatch, run: Run
) -> dict[str, Callable[[BaseException | type[BaseException]], None]]:
    return {
        "fenced": lambda e: setattr(run, "fenced", raiser(e)),
        "class_of": lambda e: monkeypatch.setattr(spotdown_carry, "class_of", araiser(e)),
        "load": lambda e: monkeypatch.setattr(spotdown_carry.song_list, "load", araiser(e)),
        "spotdown_music": lambda e: monkeypatch.setattr(
            spotdown_carry, "spotdown_music", araiser(e)
        ),
    }


CARRY_TARGETS = ("fenced", "class_of", "load", "spotdown_music")


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("target", CARRY_TARGETS)
async def test_an_error_of_any_type_in_each_carry_call_propagates(
    carry: Carry, monkeypatch: pytest.MonkeyPatch, target: str, error: type[Exception]
) -> None:
    run = Run()
    _carry_targets(monkeypatch, run)[target](error("injected vid00000001 secret"))
    with pytest.raises(error):
        await carry.carry(run, ALL)


@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("target", CARRY_TARGETS)
async def test_a_stop_a_lost_lease_and_a_role_error_pass_the_carry(
    carry: Carry, monkeypatch: pytest.MonkeyPatch, target: str, stop: Exception
) -> None:
    run = Run()
    _carry_targets(monkeypatch, run)[target](stop)
    with pytest.raises(type(stop)):
        await carry.carry(run, ALL)


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
def test_an_error_of_any_type_in_each_kept_call_propagates(
    monkeypatch: pytest.MonkeyPatch, target: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        _kept_targets(monkeypatch)[target](error("injected"))


@pytest.mark.parametrize("stop", PASSED, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("target", KEPT_TARGETS)
def test_a_stop_a_lost_lease_and_a_role_error_pass_the_kept_carry(
    monkeypatch: pytest.MonkeyPatch, target: str, stop: Exception
) -> None:
    with pytest.raises(type(stop)):
        _kept_targets(monkeypatch)[target](stop)
