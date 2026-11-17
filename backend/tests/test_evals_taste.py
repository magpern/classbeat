"""The music taste in the suite (issue #1140): the song case with stated eras finds its candidates
with ``find_songs`` against the synthetic catalogue (which answers ``track:"..." artist:"..."``
queries) and is judged by the backend's own check of the chosen list. A recorded ``FakeProvider``
script only: no model, no Spotify, no MOWL, no network."""

import dataclasses

from agentfake import CONFIG
from classbeat.evals.pick import (
    PickCase,
    PickCaseResult,
    SyntheticCatalogue,
    plan_for,
    run_pick_case,
)
from classbeat.evals.pick_checks import pick_checks
from classbeat.evals.pick_world import CatalogueSpec
from classbeat.evals.suite import load_suite
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from songscripts import provider_for

SUITE = load_suite()
CASE = SUITE.song_cases["taste_eras_45"]
LIMITS = (POOL_LIMITS, CHOOSE_LIMITS)
BANDS = SUITE.manifest.bpm_bands


async def run(case: PickCase) -> tuple[PickCaseResult, SyntheticCatalogue]:
    result, catalogue, _ = await run_pick_case(
        case, provider_for(case), CONFIG, LIMITS, SUITE.manifest.bpm_bands
    )
    return result, catalogue


def test_the_case_is_a_song_choice_case_with_three_stated_eras() -> None:
    assert "taste_eras_45" in SUITE.manifest.cases_of("song_choice", "songs")
    taste = CASE.brief.music_taste
    assert taste is not None
    assert [(e.year_from, e.year_to) for e in taste.eras] == [
        (1980, 1989),
        (1990, 1999),
        (2000, 2009),
    ]
    assert (taste.language, taste.hits) == ("sv", True)


async def test_the_case_passes_its_script_with_find_songs_and_keeps_every_era() -> None:
    result, catalogue = await run(CASE)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert [c.detail for c in result.checks if c.name == "taste_eras"] == ["every era kept"]
    # Every search was a find_songs query, answered by the catalogue's field filters.
    assert catalogue.queries
    assert all(q.startswith('track:"') and ' artist:"' in q for q in catalogue.queries)
    assert result.pool is not None
    assert all(p.year is not None and 1980 <= p.year <= 2009 for p in result.pool.picks)


async def test_a_list_that_misses_an_era_fails_only_the_taste_check() -> None:
    result, catalogue = await run(CASE)
    assert result.pool is not None
    plan = plan_for(CASE)
    passed = {c.name for c in pick_checks(CASE, plan, result, catalogue, "", BANDS) if c.ok}

    def moved(year: int | None) -> int | None:  # every 80s song is now from 1995
        return 1995 if year is not None and year < 1990 else year

    picks = tuple(dataclasses.replace(p, year=moved(p.year)) for p in result.pool.picks)
    altered = dataclasses.replace(result, pool=dataclasses.replace(result.pool, picks=picks))
    checks = pick_checks(CASE, plan, altered, catalogue, "", BANDS)
    failed = [c for c in checks if not c.ok]
    assert [c.name for c in failed] == ["taste_eras"]
    assert failed[0].detail == "era_too_few"
    assert {c.name for c in checks if c.ok} == passed - {"taste_eras"}


def test_the_catalogue_answers_field_queries_by_whole_words() -> None:
    catalogue = SyntheticCatalogue(CatalogueSpec(size=20, first_year=1980, year_span=30))
    found = catalogue.search('track:"Smoke Song 1" artist:"Smoke Artist 0" year:1980-1989')
    assert [h["title"] for h in found] == ["Smoke Song 1"]  # not "Smoke Song 10" or "11"
    assert catalogue.search('track:"Smoke Song 1" artist:"Smoke Artist 0" year:1990-1999') == []
    assert catalogue.search('track:"Smoke Song 3" artist:"Smoke Artist 1"') == []
    assert len(catalogue.search("smoke song 1")) == 10  # plain words: the old substring match
