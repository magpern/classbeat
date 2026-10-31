"""The song-choice cases of the suite and their quality checks (ADR 0012 section 4.1, issue #208).
Every case replays a recorded ``FakeProvider`` script against the synthetic catalogue; no model,
no Spotify, no MOWL, no network."""

import dataclasses
import inspect
import sys
from typing import Any

import pytest

from agentfake import CONFIG
from classbeat.agent.choose_songs import ChosenList
from classbeat.evals import pick, song_checks
from classbeat.evals.pick import (
    PickCase,
    PickCaseResult,
    SyntheticCatalogue,
    plan_for,
    run_pick_case,
)
from classbeat.evals.runner import song_suite_row
from classbeat.evals.song_checks import Band, PoolRow, SongFacts
from classbeat.evals.suite import load_suite
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from songscripts import BANDS, provider_for, script_for

SUITE = load_suite()
NEW = [
    "thin_catalogue_20", "short_15", "easy_songs_30", "long_songs_60", "climb_heavy_45",
    "sprint_heavy_45", "era_2000_2012", "replace_three",
]  # fmt: skip
CASES = {n: SUITE.song_cases[n] for n in NEW}
LIMITS = (POOL_LIMITS, CHOOSE_LIMITS)


async def run(case: PickCase, flaw: str | None = None) -> tuple[PickCaseResult, SyntheticCatalogue]:
    result, catalogue, _ = await run_pick_case(
        case, provider_for(case, flaw), CONFIG, LIMITS, SUITE.manifest.bpm_bands
    )
    return result, catalogue


def test_the_suite_has_eight_song_choice_cases_that_run_the_quality_checks() -> None:
    listed = SUITE.manifest.categories["song_choice"]["songs"]
    assert set(NEW) <= set(listed)
    assert all(c.quality for c in CASES.values())
    assert {c.catalogue.size for c in CASES.values()} >= {10, 60}  # a thin one and a long one
    assert [c for c in CASES.values() if c.replace]
    assert [c for c in CASES.values() if c.expect.years]
    assert {c.brief.length_min for c in CASES.values()} >= {15, 20, 30, 45, 60}
    assert set(SUITE.manifest.bpm_bands) == set(BANDS)


@pytest.mark.parametrize("name", NEW)
async def test_the_case_passes_its_script_on_every_check(name: str) -> None:
    case = CASES[name]
    assert len(script_for(case).turns) == 6
    result, _ = await run(case)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    names = {c.name for c in result.checks}
    assert {"pool_size", "pool_roles", "pool_unique", "bpm_band", "energy", "total"} <= names
    assert ("pool_years" in names) == bool(case.expect.years)
    assert ("kept_in_place" in names) == bool(case.replace)
    assert set(result.scores) == {"bpm_in_band", "length_error"}
    assert result.scores["bpm_in_band"] == 1.0
    assert 0 <= result.scores["length_error"] <= 0.10
    assert result.passed


async def test_a_climb_outside_its_band_fails_only_the_band_check() -> None:
    result, _ = await run(CASES["long_songs_60"], "band")
    assert result.outcome == "submitted"
    assert result.failed_checks == ["bpm_band"]
    assert not result.passed
    assert result.scores["bpm_in_band"] < 1.0


async def test_a_pool_out_of_the_era_fails_only_the_years_check() -> None:
    result, _ = await run(CASES["era_2000_2012"], "years")
    assert result.failed_checks == ["pool_years"]
    assert not result.passed


async def test_a_replace_run_judges_only_the_new_songs() -> None:
    result, _ = await run(CASES["replace_three"])
    assert result.chosen is not None
    assert sum(s.new for s in result.chosen.songs) == 3
    assert [c.detail for c in result.checks if c.name == "bpm_band"] == ["3 of 3 in band"]


# --- each check fails on data that breaks its rule ---------------------------------------------


def facts_of(result: PickCaseResult, catalogue: SyntheticCatalogue, case: PickCase) -> SongFacts:
    assert result.pool is not None
    by_id = catalogue.by_id()
    rows = [
        PoolRow(p.role, by_id[p.spotify_id].title, by_id[p.spotify_id].artist,
                by_id[p.spotify_id].year)
        for p in result.pool.picks
    ]  # fmt: skip
    return SongFacts(
        rows,
        catalogue.queries,
        {t.spotify_id: len(t.drops) for t in result.analysed},
        (case.expect.years[0], case.expect.years[1]) if case.expect.years else None,
    )


def verdicts(case: PickCase, chosen: ChosenList, facts: SongFacts) -> dict[str, bool]:
    rows = song_checks.song_checks(plan_for(case), chosen, facts, BANDS)
    return {n: ok for n, ok, _ in rows}


async def test_each_pool_check_fails_on_a_pool_that_breaks_its_rule() -> None:
    case = CASES["era_2000_2012"]
    result, catalogue = await run(case)
    assert result.chosen is not None
    chosen, facts = result.chosen, facts_of(result, catalogue, case)
    assert all(verdicts(case, chosen, facts).values())
    rows = list(facts.pool)

    def bad(**changes: Any) -> dict[str, bool]:
        return verdicts(case, chosen, dataclasses.replace(facts, **changes))

    assert not bad(pool=rows[:2])["pool_size"]
    assert not bad(pool=rows * 4)["pool_size"]
    no_climb = [dataclasses.replace(r, role="build") if r.role == "climb" else r for r in rows]
    assert not bad(pool=no_climb)["pool_roles"]
    twin = dataclasses.replace(rows[1], title=rows[0].title, artist=rows[0].artist)
    assert not bad(pool=[rows[0], twin, *rows[2:]])["pool_unique"]
    assert not bad(pool=[dataclasses.replace(rows[0], year=1999), *rows[1:]])["pool_years"]
    assert not bad(queries=[*facts.queries, "group0 year:1990-2024"])["pool_years"]


async def test_the_band_check_fails_on_a_slow_sprint_and_on_a_sprint_with_no_drop() -> None:
    case = CASES["long_songs_60"]
    result, catalogue = await run(case)
    assert result.chosen is not None
    chosen, facts = result.chosen, facts_of(result, catalogue, case)
    songs = list(chosen.songs)
    slow = [dataclasses.replace(s, bpm=90.0) if s.role == "sprint" else s for s in songs]
    assert not verdicts(case, dataclasses.replace(chosen, songs=tuple(slow)), facts)["bpm_band"]
    no_drops = dataclasses.replace(facts, drops={k: 0 for k in facts.drops})
    assert not verdicts(case, chosen, no_drops)["bpm_band"]


async def test_the_energy_check_needs_a_peak_above_the_opening_and_the_closing_song() -> None:
    case = CASES["long_songs_60"]
    result, catalogue = await run(case)
    assert result.chosen is not None
    chosen, facts = result.chosen, facts_of(result, catalogue, case)
    songs = list(chosen.songs)
    assert verdicts(case, chosen, facts)["energy"]

    def energy(new: list[Any]) -> bool:
        return verdicts(case, dataclasses.replace(chosen, songs=tuple(new)), facts)["energy"]

    assert not energy([dataclasses.replace(songs[0], bpm=400.0), *songs[1:]])
    assert not energy([*songs[:-1], dataclasses.replace(songs[-1], bpm=400.0)])
    low = [
        dataclasses.replace(s, bpm=60.0) if s.role not in song_checks.MAIN_EXCLUDED else s
        for s in songs
    ]
    assert not energy(low)
    calm = [dataclasses.replace(s, role="build") for s in songs[1:-1]]
    assert energy([songs[0], *calm, songs[-1]])  # no main part: nothing to judge


def test_a_band_holds_its_range_its_double_and_its_drops() -> None:
    climb, sprint = BANDS["climb"], BANDS["sprint"]
    assert climb.holds(70, 2)
    assert climb.holds(140, 2)
    assert not climb.holds(100, 2)
    assert not climb.holds(161, 2)
    assert sprint.holds(180, 1)
    assert not sprint.holds(119, 2)
    assert not sprint.holds(180, 0)
    with pytest.raises(ValueError, match="not below"):
        Band(low=90, high=80)


async def test_a_quality_case_without_the_band_table_is_refused() -> None:
    case = CASES["short_15"]
    with pytest.raises(ValueError, match="band table"):
        await run_pick_case(case, provider_for(case), CONFIG, LIMITS)


async def test_the_scores_are_written_to_the_results_row() -> None:
    case = CASES["long_songs_60"]
    good, _ = await run(case)
    bad, _ = await run(case, "band")
    row = song_suite_row(good, "song_choice", repeat=0, model=CONFIG.model, effort=CONFIG.effort)
    assert row["scores"] == good.scores
    assert set(row["scores"]) == {"bpm_in_band", "length_error"}
    worse = song_suite_row(bad, "song_choice", repeat=0, model=CONFIG.model, effort=CONFIG.effort)
    assert worse["scores"]["bpm_in_band"] < row["scores"]["bpm_in_band"]
    assert "bpm_band" in {c["name"] for c in row["checks"]}


# --- the runner takes any provider and reaches no Spotify or MOWL ------------------------------


def test_the_runner_takes_a_provider_and_nothing_that_reaches_an_account() -> None:
    params = inspect.signature(run_pick_case).parameters
    assert list(params) == ["case", "provider", "config", "limits", "bands"]
    assert "ModelProvider" in str(params["provider"].annotation)
    assert not hasattr(pick, "spotify_web")


@pytest.mark.parametrize("name", ["long_songs_60", "replace_three", "era_2000_2012"])
async def test_a_song_run_calls_nothing_of_spotify_mowl_or_the_connections(name: str) -> None:
    forbidden = (
        "mowl.api", "mowl.spotify_web", "mowl.sync", "mowl.library", "classbeat.connections",
        "classbeat.spotify", "classbeat.songs", "classbeat.playlists", "classbeat.egress",
        "classbeat.operations", "classbeat.saving",
    )  # fmt: skip
    called: set[str] = set()

    def profile(frame: Any, event: str, arg: Any) -> None:
        if event == "call":
            module = frame.f_globals.get("__name__", "")
            if module.startswith(forbidden):
                called.add(f"{module}.{frame.f_code.co_name}")

    case = CASES[name]
    provider = provider_for(case)
    sys.setprofile(profile)
    try:
        result, _, _ = await run_pick_case(case, provider, CONFIG, LIMITS, SUITE.manifest.bpm_bands)
    finally:
        sys.setprofile(None)
    assert result.passed
    assert called == set()
