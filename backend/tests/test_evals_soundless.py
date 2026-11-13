"""The soundless eval cases (ADR 0007 amendment of 2026-11-03, item 6; issue #531): the case
model, the runner giving a soundless case the soundless stages as a job gets them, the checks
that fail on a draft that names music or follows a beat, and the suite's coverage rules. No model,
no network: the runs replay recorded ``FakeProvider`` scripts."""

import copy
import dataclasses
import json
import re
from typing import Any
from uuid import uuid4

import pytest

from agentfake import CONFIG
from classbeat.agent.limits import Limits
from classbeat.agent.loop import RunInfo, StageOutcome
from classbeat.agent.provider import Usage
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.soundless import check_soundless
from classbeat.evals import suite
from classbeat.evals.cases import Case, load_cases
from classbeat.evals.checks import MUSIC_WORDS, names_music, run_checks
from classbeat.evals.runner import CaseResult, run_case
from classbeat.models.versions import all_prompt_versions
from smokescripts import good_draft
from soundlessscripts import provider_for_case

SOUNDLESS = {c.name: c for c in load_cases() if c.soundless}
SUITE = suite.load_suite()
DESIGN, SPRINTS, REVISE, ADVERSARIAL = (
    SOUNDLESS[n]
    for n in (
        "soundless_45",
        "soundless_30_sprints",
        "soundless_revise_sprints",
        "soundless_adversarial",
    )
)


def raw(case: Case) -> dict[str, Any]:
    """The case as its JSON file holds it (what a soundless case may and may not say)."""
    data = case.model_dump(mode="json", exclude={"music"})
    return data


# --- the case model ------------------------------------------------------------------------------


def test_the_soundless_cases_are_the_four_of_the_issue_in_their_categories() -> None:
    assert set(SOUNDLESS) == {
        "soundless_45", "soundless_30_sprints", "soundless_revise_sprints", "soundless_adversarial",
    }  # fmt: skip
    index = SUITE.manifest.case_index()
    assert {n: index[n] for n in SOUNDLESS} == {
        "soundless_45": ("soundless", "design"),
        "soundless_30_sprints": ("soundless", "design"),
        "soundless_revise_sprints": ("soundless", "design"),
        "soundless_adversarial": ("adversarial", "design"),
    }
    assert (DESIGN.brief.length_min, SPRINTS.brief.length_min) == (45, 30)
    assert SPRINTS.brief.sprints == 2
    assert REVISE.revise is not None
    assert REVISE.expect.revision is not None
    assert ADVERSARIAL.expect.forbidden_text
    assert suite.CATEGORY_GROUPS["soundless"] == frozenset({"design"})
    assert all(c.brief.sport == "cycling" for c in SOUNDLESS.values())


def test_a_soundless_case_names_no_music_and_any_other_case_names_its_music() -> None:
    data = raw(DESIGN)
    assert Case.model_validate_json(json.dumps(data)).soundless
    with pytest.raises(ValueError, match="names no music"):
        Case.model_validate_json(json.dumps({**data, "music": {"tracks": 3}}))
    with pytest.raises(ValueError, match="names its music"):
        Case.model_validate_json(json.dumps({**data, "soundless": False}))


@pytest.mark.parametrize(
    "change",
    [
        {"brief": {"sport": "running", "length_min": 30, "intensity": "moderate"}},
        {"expect": {"duration": "music"}},
        {"expect": {"music_sync": {}}},
        {"expect": {"patterns": {"mode": "follows"}}},
    ],
    ids=["running", "duration_music", "music_sync", "patterns"],
)
def test_a_soundless_case_is_cycling_and_judged_on_no_music_check(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"soundless case|validation error"):
        Case.model_validate_json(json.dumps({**raw(DESIGN), **change}))


# --- the runner: the soundless stages, as a job gets them ---------------------------------------


@pytest.mark.parametrize("case", SOUNDLESS.values(), ids=lambda c: c.name)
async def test_a_soundless_case_runs_the_soundless_stage_and_the_model_sees_no_music(
    case: Case,
) -> None:
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG, zone7_tss_margin=SUITE.manifest.zone7_tss_margin)
    assert result.passed, [c.detail for c in result.checks if not c.ok]
    assert fake.remaining == 0
    spec = fake.sent[0].spec
    assert [t.name for t in spec.tools] == ["preview_draft", "submit_draft"]  # no get_music
    key = "soundless_revise" if case.revise else "soundless_design"
    assert result.prompt_version == all_prompt_versions()[key]
    assert spec.stage == ("revise" if case.revise else "design")
    preamble, _, payload = spec.first_message.partition("{")
    assert "music" not in json.loads("{" + payload)  # the model's input has no music key
    # Everything but the instructor's own words (the injection case's brief text names a playlist).
    # The game "racetrack" is not a music track: strip that one word, keep the substring ban.
    text = re.sub("racetrack", "", (spec.system + preamble), flags=re.IGNORECASE).lower()
    for word in ("playlist", "get_music", "bpm", "track", "smoke song", "smoke artist", "song"):
        assert word not in text, word
    assert "no music" in spec.first_message
    assert case.brief.sport == "cycling"
    assert all("track" not in s for seg in result.timeline for s in seg["intervals"])


async def test_the_injection_is_data_to_the_soundless_model_and_its_foreign_calls_are_refused() -> (
    None
):
    fake = provider_for_case(ADVERSARIAL)
    result = await run_case(ADVERSARIAL, fake, CONFIG)
    assert result.passed
    sent = fake.sent[0].spec
    assert "PWNED" in sent.first_message  # in the data block of the first message
    assert "PWNED" not in sent.system  # never in the instructions
    assert result.tool_calls == 5  # the three calls it was talked into, a preview and the submit
    first_results = [r for results, _ in fake.sent[0].turns for r in results]
    refused = [r for r in first_results if r.is_error and "unknown_tool" in r.content]
    assert len(refused) == 3


# --- the checks ------------------------------------------------------------------------------


def outcome() -> StageOutcome:
    return StageOutcome(
        None, uuid4(), RunInfo("design", "fake", "m", "e", "0" * 64), 3, 3, Usage(), 100, "m"
    )


def checked(
    case: Case, draft: dict[str, Any] | None = None, *, note: str = "", **changes: Any
) -> dict[str, bool]:
    """The checks of a soundless draft (the good one unless given), by name."""
    result = check_soundless(WorkoutDraft.model_validate(draft or good_draft(case)))
    result = dataclasses.replace(result, **changes)
    found = run_checks(case, result, outcome(), Limits(), float("inf"), note)
    return {c.name: c.ok for c in found}


def test_the_good_draft_passes_every_soundless_check_and_judges_nothing_on_music() -> None:
    ok = checked(DESIGN)
    assert all(ok.values()), ok
    assert "changes_on_music" not in ok
    assert "quality_issues" not in ok
    assert {"no_beat", "no_music_reference", "duration", "zones", "tss", "cooldown"} <= set(ok)


def edited(case: Case, edit: Any) -> dict[str, Any]:
    draft = copy.deepcopy(good_draft(case))
    edit(draft)
    return draft


def test_a_draft_that_names_music_fails_no_music_reference() -> None:
    for where in ("name", "description", "segment", "cue"):

        def edit(d: dict[str, Any], where: str = where) -> None:
            if where == "segment":
                d["segments"][1]["name"] = "Climb to the beat"
            elif where == "cue":
                d["segments"][1]["intervals"][0]["cue"] = "Ride with the song"
            else:
                d[where] = "A class set to your favourite playlist"

        ok = checked(DESIGN, edited(DESIGN, edit))
        assert ok["no_music_reference"] is False, where
        assert ok["validate"], where  # nothing else about the draft is wrong
        assert ok["duration"], where
    assert checked(REVISE, note="Swapped a song for a longer climb.")["no_music_reference"] is False
    assert checked(DESIGN, note="Added a second sprint block.")["no_music_reference"] is True


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("Ride to the beat", True),
        ("Drop the gear and stay on track", False),
        ("Beat your best", False),
        ("Spotify playlist", True),
        ("Hold zone 3 for the whole song", True),
        ("Standing climb, heavy gear", False),
        ("This class has no music", False),
        ("Ridden without music, steady", False),
        ("No music: stay at 90 rpm", False),
        ("Ride without music to the beat", True),
        ("No music, but sing the chorus", True),
        ("Keep your heart rate near 150 bpm", False),
        ("A 128 bpm track", True),
        ("Fine-tune the resistance", False),
        ("Fine-tuning your cadence", False),
        ("Hum a tune and keep going", True),
        ("Take the drop in resistance slowly", False),
        ("The drop ahead is steep", False),
        ("Hold on until the drop hits", True),
        ("Wait for the bass drop", True),
    ],
)
def test_the_music_words_name_music_and_nothing_else(text: str, names: bool) -> None:
    assert names_music(text) is names


def test_the_music_words_alone_do_not_forgive_no_music() -> None:
    # The statement that there is none is cut out by ``names_music``, not by ``MUSIC_WORDS``.
    assert MUSIC_WORDS.search("no music")
    assert not names_music("no music")


def test_a_draft_that_says_it_has_no_music_passes_no_music_reference() -> None:
    said = edited(
        DESIGN, lambda d: d["segments"][1]["intervals"][0].update(cue="No music, ride by feel")
    )
    assert checked(DESIGN, said)["no_music_reference"] is True
    assert checked(DESIGN, note="Ridden without music")["no_music_reference"] is True
    assert checked(DESIGN, note="Ride to the song")["no_music_reference"] is False


@pytest.mark.parametrize(
    ("what", "edit"),
    [
        ("changes_on_music_min", lambda d: d["expect"].update(changes_on_music_min=0.5)),
        ("allow_issues", lambda d: d["expect"].update(allow_issues=["zone_run_too_long"])),
        ("songs_requested", lambda d: d["revise"].update(outcome="songs_requested")),
    ],
)
def test_a_soundless_case_refuses_what_only_a_music_case_can_use(what: str, edit: Any) -> None:
    data = raw(REVISE)
    Case.model_validate_json(json.dumps(data))  # the case as it is, valid
    edit(data)
    with pytest.raises(ValueError, match="soundless"):
        Case.model_validate_json(json.dumps(data))


def test_a_music_case_still_takes_those_settings() -> None:
    plain = next(c for c in load_cases() if not c.soundless and c.revise is None)
    data = plain.model_dump(mode="json")
    data["expect"].update(changes_on_music_min=0.5, allow_issues=["zone_run_too_long"])
    case = Case.model_validate_json(json.dumps(data))
    assert case.expect.changes_on_music_min == 0.5


def test_the_digest_records_a_pattern_with_its_flags() -> None:
    import re

    plain = suite._plain("X", re.compile("music"))  # pyright: ignore[reportPrivateUsage]
    loose = suite._plain("X", re.compile("music", re.IGNORECASE))  # pyright: ignore[reportPrivateUsage]
    assert plain != loose
    assert loose["pattern"] == "music"
    assert suite.check_constants()["checks.MUSIC_WORDS"]["flags"] & re.IGNORECASE
    _, cases, sources, constants = (
        suite.read_manifest_data(),
        {},
        {},
        copy.deepcopy(suite.check_constants()),
    )
    base = suite.suite_digest({}, cases, sources, constants)
    constants["checks.MUSIC_WORDS"]["flags"] = 0
    assert suite.suite_digest({}, cases, sources, constants) != base


def test_a_beat_cadence_fails_no_beat_even_though_the_schema_refuses_it() -> None:
    result = check_soundless(WorkoutDraft.model_validate(good_draft(DESIGN)))
    spec = json.loads(json.dumps(result.spec))
    spec["segments"][1]["intervals"][0]["rpm"] = "beat"
    ok = checked(DESIGN, spec=spec)
    assert ok["no_beat"] is False
    assert checked(DESIGN)["no_beat"] is True


def test_the_length_zone_and_tss_rules_apply_to_a_soundless_class() -> None:
    short = checked(DESIGN, duration_sec=1800)
    assert short["duration"] is False
    easy = edited(
        DESIGN, lambda d: [i.update(zone=1) for s in d["segments"] for i in s["intervals"]]
    )
    ok = checked(DESIGN, easy)
    assert (ok["tss"], ok["zones"]) == (False, False)
    hard = edited(
        DESIGN, lambda d: [i.update(zone=5) for s in d["segments"][1:-1] for i in s["intervals"]]
    )
    assert checked(DESIGN, hard)["zones"] is False
    flat = {k: v for k, v in good_draft(SPRINTS).items()}
    flat["segments"] = [s for s in flat["segments"] if not s["name"].startswith("Sprint")]
    assert checked(SPRINTS, flat)["sprints_and_races"] is False


def test_a_soundless_run_that_fails_its_music_check_is_a_failed_case() -> None:
    bad = edited(DESIGN, lambda d: d["segments"][1]["intervals"][0].update(cue="Ride to the beat"))
    result = check_soundless(WorkoutDraft.model_validate(bad))
    found = run_checks(DESIGN, result, outcome(), Limits(), float("inf"))
    failed = CaseResult("soundless_45", "submitted", found)
    assert failed.failed_checks == ["no_music_reference"]
    assert not failed.passed


# --- the suite: the digest and the coverage rule -----------------------------------------------


def test_the_suite_is_current_and_its_digest_covers_the_soundless_parts() -> None:
    assert SUITE.manifest.suite_version == 22
    assert "agent/soundless_input.py" in suite.RULE_SOURCES
    data, cases, sources, constants = (
        suite.read_manifest_data(),
        {
            n: c.model_dump(mode="json")
            for n, c in {**SUITE.design_cases, **SUITE.song_cases, **SUITE.interpret_cases}.items()
        },
        suite.digest_sources(),
        suite.check_constants(),
    )
    assert "classbeat/agent/soundless_input.py" in sources
    assert "checks.MUSIC_WORDS" in constants
    base = suite.suite_digest(data, cases, sources, constants)
    assert base == SUITE.manifest.digest
    changes: list[Any] = [
        lambda d, c, s, k: c["soundless_45"]["brief"].update(length_min=46),
        lambda d, c, s, k: c["soundless_adversarial"]["expect"]["forbidden_text"].append("x"),
        lambda d, c, s, k: s.update(
            {"classbeat/agent/soundless_input.py": s["classbeat/agent/soundless_input.py"] + " "}
        ),
        lambda d, c, s, k: k.update({"checks.MUSIC_WORDS": "music"}),
        lambda d, c, s, k: d["categories"].pop("soundless"),
        lambda d, c, s, k: d["categories"]["soundless"]["design"].pop(),
    ]
    for edit in changes:
        d, c, s, k = copy.deepcopy((data, cases, sources, constants))
        edit(d, c, s, k)
        assert suite.suite_digest(d, c, s, k) != base
