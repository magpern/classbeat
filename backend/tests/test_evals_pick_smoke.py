"""The song-picking cases of the smoke set in CI (ADR 0008 section 3, issue #179): a pick, a
replace and an injected title, each through the real ``pick_pool`` and ``choose_songs`` stages
against a recorded ``FakeProvider`` script, a synthetic Spotify catalogue and a synthetic MOWL
analysis. No model, no network. Also: the checks fail on lists that break a rule."""

import dataclasses

import pytest

from agentfake import CONFIG
from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.limits import Limits
from classbeat.evals.cases import load_cases
from classbeat.evals.pick import (
    PickCase,
    PickCaseResult,
    SyntheticCatalogue,
    load_pick_cases,
    pick_checks,
    plan_for,
    run_pick_case,
    sent_text,
)
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from pickscripts import provider_for_case, script_for

CASES = load_pick_cases()
BY_NAME = {c.name: c for c in CASES}
LIMITS = (POOL_LIMITS, CHOOSE_LIMITS)


def test_the_smoke_set_has_the_three_pick_cases_beside_the_eighteen() -> None:
    assert [c.name for c in CASES] == ["injected_title", "pick_30", "replace_two"]
    assert len(load_cases()) == 18  # the design and revise cases are as they were
    for case in CASES:
        assert script_for(case).turns


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_case_passes_against_its_recorded_script(case: PickCase) -> None:
    fake = provider_for_case(case)
    result, _, registry = await run_pick_case(case, fake, CONFIG, LIMITS)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.passed
    assert fake.remaining == 0  # the script was played to its end
    assert result.turns == len(script_for(case).turns)
    assert result.cost_micros > 0
    assert set(result.prompt_versions) == {"pick_pool", "choose_songs"}
    assert all(len(v) == 64 for v in result.prompt_versions.values())
    assert len(registry) > 0


async def test_the_pick_stores_the_analysis_and_the_replace_keeps_its_slots() -> None:
    result, catalogue, _ = await run_pick_case(
        BY_NAME["pick_30"], provider_for_case(BY_NAME["pick_30"]), CONFIG, LIMITS
    )
    assert result.chosen is not None
    chosen = result.chosen
    assert (chosen.total_sec, chosen.mode, len(chosen.songs)) == (1857, "pick", 8)
    assert [s.title for s in chosen.songs] == [
        f"Smoke Song {i}" for i in (0, 2, 4, 10, 6, 7, 11, 12)
    ]
    assert {s.spotify_id for s in chosen.songs} <= set(catalogue.by_id())
    result, _, _ = await run_pick_case(
        BY_NAME["replace_two"], provider_for_case(BY_NAME["replace_two"]), CONFIG, LIMITS
    )
    assert result.chosen is not None
    rep = result.chosen
    assert rep.mode == "replace"
    assert [s.new for s in rep.songs] == [False, False, True, False, False, True, False, False]
    assert [s.title for s in rep.songs if not s.new] == [
        f"Kept Song {i}" for i in (0, 1, 3, 4, 6, 7)
    ]


async def test_the_injected_title_case_sends_the_text_as_data_only() -> None:
    case = BY_NAME["injected_title"]
    fake = provider_for_case(case)
    result, _, _ = await run_pick_case(case, fake, CONFIG, LIMITS)
    assert result.passed
    sent = sent_text(fake)
    assert "PWNED" in sent
    assert "admin mode" in sent
    for stage in fake.sent:
        assert "PWNED" not in stage.spec.system
        assert "admin mode" not in stage.spec.system
    for char in ("‮", "\u0000", "​", "\u0007"):
        assert char not in sent
    assert result.chosen is not None
    assert not any(f.lower() in result.chosen.note.lower() for f in case.expect.forbidden_text)


# --- the checks fail on lists that break a rule ------------------------------------------------


async def good() -> tuple[PickCase, PickCaseResult, SyntheticCatalogue, str]:
    case = BY_NAME["replace_two"]
    fake = provider_for_case(case)
    result, catalogue, _ = await run_pick_case(case, fake, CONFIG, LIMITS)
    return case, result, catalogue, sent_text(fake)


def ok_of(
    case: PickCase, result: PickCaseResult, catalogue: SyntheticCatalogue, sent: str
) -> dict[str, bool]:
    return {c.name: c.ok for c in pick_checks(case, plan_for(case), result, catalogue, sent)}


async def test_the_checks_pass_on_the_good_run_and_fail_on_each_broken_one() -> None:
    case, result, catalogue, sent = await good()
    base = ok_of(case, result, catalogue, sent)
    assert all(base.values())
    assert len(base) == 9
    assert result.chosen is not None
    songs = list(result.chosen.songs)

    def broken(new: list[ChosenSong], **changes: str) -> dict[str, bool]:
        chosen: ChosenList = dataclasses.replace(result.chosen, songs=tuple(new), **changes)  # pyright: ignore[reportArgumentType]
        return ok_of(case, dataclasses.replace(result, chosen=chosen), catalogue, sent)

    assert not broken(songs[:3])["total"]
    assert not broken([*songs[:-1], songs[0]])["no_repeat"]
    assert not broken([*songs[:-1], dataclasses.replace(songs[-1], role="recovery")])["ends"]
    assert not broken([dataclasses.replace(songs[0], title="Invented by the model"), *songs[1:]])[
        "from_analysis"
    ]
    assert not broken([songs[1], songs[0], *songs[2:]])["kept_in_place"]
    assert not broken([dataclasses.replace(s, new=True) for s in songs])["replaced_are_new"]
    assert (
        not broken(songs, note="PWNED approved admin mode")["forbidden_text"]
        or not case.expect.forbidden_text
    )
    leaked = ok_of(case, result, catalogue, sent + " SmokePick0000000000003")
    assert not leaked["no_ids_sent"]
    kept_leak = ok_of(case, result, catalogue, sent + " SmokeKept0000000000001")
    assert not kept_leak["no_ids_sent"]


async def test_a_run_that_fails_is_a_failed_case_with_its_category() -> None:
    case = BY_NAME["pick_30"]
    tight = (Limits(max_turns=1, max_tool_calls=30, max_corrections=3), CHOOSE_LIMITS)
    result, _, _ = await run_pick_case(case, provider_for_case(case), CONFIG, tight)
    assert result.outcome == "limit_exceeded"
    assert not result.passed
    assert result.checks == []
