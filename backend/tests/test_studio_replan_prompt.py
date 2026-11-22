"""#1372 (ADR 0036 amendment B5 and B6): the studio prompts say the mix is a suggestion and that
only an ``exact`` track may be cut; a cut on any other track is the soft hint
``cut_on_inexact_track``; a re-plan's revise message states the user's ``MixChoices`` first, by
list index only. Pure: no I/O, no model."""

from typing import Any

import pytest

from agentfake import good_draft
from classbeat.agent import studio_mix
from classbeat.agent.stage import ToolAnswer
from classbeat.agent.studio_design import StudioBase, StudioContext, studio_stage
from classbeat.agent.studio_mix import StudioTrack
from classbeat.agent.studio_prompt import DESIGN_SYSTEM, REVISE, REVISE_SYSTEM
from classbeat.agent.studio_replan import HEAD, choices_section
from classbeat.agent.studio_schemas import MixInput, StudioDraftInput
from classbeat.studio import mix_hint_policy
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, on_list
from classbeat.studio.mix_facts_model import TrackFacts
from studiomaps import track_map, whole_mix
from test_studio_stage import BRIEF

TAIL = (
    "Re-plan everything else (the other tracks' cuts, the order, the crossfades the form allows "
    "and the workout) so the class fits the brief's length. Your result is a new proposal: these "
    "choices are applied to it afterwards, and whatever breaks one is undone.\n\n"
)
HEADLINE = "The instructor's mix choices for this re-plan (tracks by index):\n"


def per_song(keep: str = "none", cut_in: str = "none", cut_out: str = "none") -> str:
    return (
        f"- keep whole (once, from 0 to its length): {keep}\n"
        f"- hard cut in (crossfade 0 on the change into each of its pieces): {cut_in}\n"
        f"- hard cut out (crossfade 0 on the change out of each of its pieces): {cut_out}\n"
    )


WHOLE_SONGS = (
    HEADLINE
    + "- form: whole songs, no crossfades, no trims: every track plays once, whole (start_sec 0, "
    "end_sec its length), and every crossfade is 0\n" + TAIL
)


def choices(**given: Any) -> MixChoices:
    return MixChoices.model_validate({"dj_mix": True, "blend": True, "trim": True, **given})


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (MixChoices.whole_songs(), WHOLE_SONGS),
        (
            choices(blend=False),
            HEADLINE + "- form: DJ mix with hard cuts only: no crossfades (every crossfade 0); "
            "songs may be trimmed\n" + per_song() + TAIL,
        ),
        (
            choices(trim=False),
            HEADLINE + "- form: DJ mix, every song once, whole: no trims and no repeats "
            "(start_sec 0, end_sec its length); crossfades allowed\n" + per_song() + TAIL,
        ),
        (
            choices(blend=False, trim=False),
            HEADLINE + "- form: DJ mix, every song once, whole, with hard cuts only: no trims, no "
            "repeats and no crossfades (every crossfade 0)\n" + per_song() + TAIL,
        ),
        (
            choices(keep_whole=(0, 3), hard_cut_in=(2,), hard_cut_out=(4, 7)),
            HEADLINE
            + "- form: DJ mix: songs may be trimmed and blended with crossfades\n"
            + per_song("tracks 0, 3", "tracks 2", "tracks 4, 7")
            + TAIL,
        ),
    ],
    ids=["whole_songs", "blend_off", "trim_off", "both_off", "per_song"],
)
def test_the_choices_section_is_pinned(given: MixChoices, expected: str) -> None:
    assert HEAD == HEADLINE
    assert choices_section(given) == expected


def test_the_whole_songs_default_says_whole_songs_no_crossfades_no_trims() -> None:
    assert "whole songs, no crossfades, no trims" in choices_section(MixChoices.whole_songs())
    assert "hard cuts only, no crossfades" not in choices_section(MixChoices.whole_songs())


def titled(n: int) -> StudioTrack:
    return StudioTrack(f"Typed Title {n} ref-0000abcd", "Typed Band", track_map(300.0 + 10 * n))


def revise_ctx(given: MixChoices | None) -> StudioContext:
    base = StudioBase(good_draft(), whole_mix(), ("Harder",), "Re-plan the other songs", given)
    return StudioContext(BRIEF, tuple(titled(n) for n in range(8)), base=base)


def test_a_replan_message_states_the_choices_first_and_by_index_only() -> None:
    given = choices(keep_whole=(1,), hard_cut_out=(5,))
    first = revise_ctx(given).first_message()
    section = choices_section(given)
    assert first.startswith(REVISE + section)  # first, before the rules and the data
    # Only fixed text and list indexes: no title, artist or reference the user's list holds.
    for typed in ("Typed Title", "Typed Band", "ref-", "Re-plan the other songs"):
        assert typed not in section
    assert first.count(HEADLINE) == 1


def test_a_chat_revise_has_no_choices_section() -> None:
    first = revise_ctx(None).first_message()
    assert first.startswith(REVISE + '{"brief"')
    assert HEADLINE not in first


def test_the_prompts_say_the_mix_is_a_suggestion_and_only_exact_tracks_are_cut() -> None:
    for system in (DESIGN_SYSTEM, REVISE_SYSTEM):
        assert "suggestions the instructor may decline" in system
        assert "Only a track with timing exact may be cut" in system
        assert "plays once, whole (start_sec 0, end_sec its length)" in system
        assert "cut_on_inexact_track" in system
        assert "Keep repeats to parts of songs (exact tracks only)" in system
    assert "mix choices (a re-plan), keep each one exactly" in REVISE_SYSTEM
    assert "mix choices (a re-plan)" not in DESIGN_SYSTEM


@pytest.mark.parametrize(
    "raw",
    [
        {"dj_mix": False, "blend": False, "trim": False, "keep_whole": [0]},  # whole songs: no set
        {"dj_mix": False, "blend": True, "trim": False},
        {"dj_mix": True, "blend": True, "trim": True, "keep_whole": [8]},  # off a list of 8
        {"dj_mix": True, "blend": True, "trim": True, "hard_cut_in": [-1]},
        {"dj_mix": True, "blend": True, "trim": True, "keep_whole": [1, 1]},
        {"dj_mix": True, "blend": True, "trim": True, "keep_whole": "0"},
        {"dj_mix": True, "blend": True, "trim": True, "keep_whole": [True]},
        {"dj_mix": True, "blend": True, "trim": True, "reference": "ref-1"},  # no unknown field
        {"dj_mix": "yes", "blend": True, "trim": True},
        {"blend": True, "trim": True},
        [],
    ],
)
def test_choices_off_the_list_or_unread_are_invalid_input(raw: object) -> None:
    with pytest.raises(MixChoiceRefused) as refused:
        on_list(raw, 8)
    assert refused.value.reason == "invalid_input"


def test_choices_on_the_list_read_in_any_order() -> None:
    raw = {"dj_mix": True, "blend": False, "trim": True, "keep_whole": [7, 0]}
    assert on_list(raw, 8).keep_whole == (0, 7)


# The hint ``cut_on_inexact_track`` (B5): soft, never a refusal.


def timed(timing: Any) -> tuple[StudioTrack, ...]:
    return tuple(
        StudioTrack(f"Song {n}", "Band", track_map(300.0 + 10 * n), timing) for n in range(8)
    )


def cut_first(mix: dict[str, Any]) -> dict[str, Any]:
    mix["tracks"][0]["end_sec"] = 200.0
    return mix


@pytest.mark.parametrize("timing", ["approximate", "unusable", None])
def test_a_cut_on_a_track_that_is_not_exact_is_hinted(timing: Any) -> None:
    composed = studio_mix.compose(timed(timing), MixInput.model_validate(cut_first(whole_mix())))
    hinted = [h for h in composed.hints if h.startswith("cut_on_inexact_track")]
    assert hinted == [mix_hint_policy.Hint("cut_on_inexact_track", (0,)).text()]
    assert "pieces 0 cut a track whose timing is not exact" in hinted[0]


def test_a_cut_on_an_exact_track_and_whole_inexact_tracks_are_not_hinted() -> None:
    exact = studio_mix.compose(timed("exact"), MixInput.model_validate(cut_first(whole_mix())))
    whole = studio_mix.compose(timed("approximate"), MixInput.model_validate(whole_mix()))
    for composed in (exact, whole):
        assert not any(h.startswith("cut_on_inexact_track") for h in composed.hints)


def test_the_hint_reaches_the_design_stages_preview_answer() -> None:
    for timing, hinted in (("approximate", True), ("exact", False)):
        tool = studio_stage(StudioContext(BRIEF, timed(timing))).tool("preview_draft")
        assert tool is not None
        given = StudioDraftInput.model_validate(
            {"draft": good_draft(), "mix": cut_first(whole_mix())}
        )
        answer = tool.handler(given)
        assert isinstance(answer, ToolAnswer)  # never a refusal
        warned = any(w.startswith("cut_on_inexact_track") for w in answer.payload["warnings"])
        assert warned is hinted


def test_the_hint_names_every_cut_piece_and_reads_whole_in_hundredths() -> None:
    facts = {"a": TrackFacts(240.004, 128.0, 0.4, "approximate"), "b": TrackFacts(200, 128, 0.4)}
    spec = {
        "tracks": [
            {"reference": "a", "start_sec": 0.0, "end_sec": 240.0},  # whole in hundredths
            {"reference": "b", "start_sec": 10.0, "end_sec": 200.0},
            {"reference": "a", "start_sec": 0.0, "end_sec": 90.0},
        ],
        "crossfades": [0, 0],
    }
    assert mix_hint_policy.inexact_cuts(spec, facts) == [
        mix_hint_policy.Hint("cut_on_inexact_track", (1, 2))
    ]
