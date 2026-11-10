"""The games section of the cycling prompts and its absence from the running ones (issue #878).

ADR 0029 section 2 rows G6, G8 and G9 are prompt guidance, so they are pinned here by their words.
Pure: the prompts are strings.
"""

import pytest

from classbeat.agent import design, games_catalog, revise, running, soundless
from classbeat.agent.game_prompt import games_section

MUSIC_PROMPTS = {"design": design.SYSTEM_PROMPT, "revise": revise.SYSTEM_PROMPT}
FIT = "fitting to the music can shorten an interval"


def test_the_section_prints_the_catalogue_and_nothing_else() -> None:
    text = games_section(music=True)
    assert games_catalog.prompt_lines() in text
    assert "ghost" not in text.lower()
    assert text.count('- "') == 4


def test_the_guidance_covers_race_type_load_heart_rate_margin_and_countdown() -> None:
    text = games_section(music=True)
    for part in (
        "A race",
        "is not a game",
        "never write any other name",
        "Never invent a type",
        "the load around a game",  # G8
        "heart rate",  # G6
        "longer games",
        "at least 10 s",
        "Leave a margin well over 10 s",
        "may announce the coming game",  # G9
        "never promise one",
    ):
        assert part in text, part


def test_the_music_margin_is_only_where_music_fits_the_draft() -> None:
    assert FIT in games_section(music=True)
    assert FIT not in games_section(music=False)


def test_only_the_revise_section_has_the_editing_operations_and_the_note() -> None:
    assert "Editing games:" in games_section(music=True, revise=True)
    assert "Editing games:" not in games_section(music=True)
    assert "say so in note" in games_section(music=True, revise=True)
    assert "say so in note" not in games_section(music=True)


@pytest.mark.parametrize("name", MUSIC_PROMPTS)
def test_the_music_cycling_prompts_carry_the_section(name: str) -> None:
    prompt = MUSIC_PROMPTS[name]
    assert games_section(music=True, revise=name == "revise") in prompt
    assert all(f'"{t}"' in prompt for t in games_catalog.TYPES)


def test_the_soundless_prompts_carry_the_section_without_the_music_margin() -> None:
    for prompt, editing in (
        (soundless.SYSTEM_PROMPT, False),
        (soundless.REVISE_SYSTEM_PROMPT, True),
    ):
        assert games_section(music=False, revise=editing) in prompt
        assert FIT not in prompt


def test_the_running_prompts_have_no_games() -> None:
    for prompt in (running.DESIGN_SYSTEM_PROMPT, running.REVISE_SYSTEM_PROMPT):
        low = prompt.lower()
        assert "game" not in low
        assert not any(t in low for t in games_catalog.TYPES)
