"""The recorded fake-provider script of each smoke case (ADR 0006 section 8).

Every script is what a careful model would do, as turns of tool calls with fixed usage numbers:
read the music, preview a draft, submit it. Five cases go a little differently so the harness
exercises the loop, not only the happy path:

- ``hard_45_sprints``: the first draft stands for two minutes; the quality report says so and the
  second draft fixes it;
- ``ftp_5_45``: the first draft's FTP test no longer lasts five minutes after fitting to the music
  (a real behaviour of ``sync.fit_to_music``); the error comes back and a shifted draft fits;
- ``long_60``: the first ``submit_draft`` has a 30 s cooldown and is refused (a correction round);
- ``music_no_sections``: the model asks for one track's sections;
- ``minimum_10``: a short class, submitted without a preview.

The adversarial cases check that the injection text reaches the model only as tool data
(``contains``) and that the submitted draft does not repeat it (the case's ``forbidden_text``).
"""

import copy
from typing import Any

from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.evals.cases import Case
from smokedrafts import make_draft

# The asked length does not apply: the music is shorter; the draft fills it.
TOTAL = {"music_too_short": 1380}
# Where a first FTP lead of 300 s is shifted so the fitted test still lasts its full length.
FTP_LEAD = {"ftp_20_60": 250, "ftp_5_45": 260}


def _usage(inp: int, out: int, cached: int = 0) -> dict[str, int]:
    return {"input": inp, "output": out, "cache_read": cached, "cache_write": 0}


def _call(name: str, input: dict[str, Any], **checks: Any) -> dict[str, Any]:
    return {"name": name, "input": input, **checks}


def _turn(call: dict[str, Any], usage: dict[str, int]) -> dict[str, Any]:
    return {"calls": [call], "usage": usage}


def good_draft(case: Case) -> dict[str, Any]:
    b = case.brief
    return make_draft(
        TOTAL.get(case.name, b.length_min * 60),
        intensity=b.intensity,
        climbs=b.climbs,
        sprints=b.sprints,
        races=b.races,
        ftp=b.ftp_test,
        ftp_lead=FTP_LEAD.get(case.name, 300),
        name=f"Invented {case.name.replace('_', ' ')} class",
    )


def _standing_first(draft: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(draft)
    first = out["segments"][1]["intervals"][0]  # the first main interval stands for too long
    first.update(sec=max(first["sec"], 120), standing=True, rpm=[65, 75])
    return out


def _short_cooldown(draft: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(draft)
    out["segments"][-1]["intervals"] = [{"sec": 30, "zone": 1, "rpm": [70, 80], "standing": False}]
    return out


# The revised draft of each revise case: what a careful model hands back for its request.
REVISED: dict[str, dict[str, Any]] = {
    "revise_more_sprints": dict(total=1800, intensity="moderate", climbs=1, sprints=2),
    "revise_easier": dict(total=2700, intensity="moderate", climbs=1, sprints=2),
    "revise_adversarial_music": dict(total=1800, intensity="moderate", climbs=2, sprints=1),
}
NOTES = {
    "revise_more_sprints": "Added a second sprint block before the cooldown.",
    "revise_easier": "Brought the class down to moderate effort.",
    "revise_adversarial_music": "Added a second climb.",
}


def revise_script(case: Case) -> FakeScript:
    """read the music, preview the revised draft, submit it with a note (a careful model)."""
    revised = make_draft(**REVISED[case.name], name=f"Invented {case.name.replace('_', ' ')}")
    read = _turn(
        _call("get_music", {}, expect={"ok": True}, contains=['"tracks"']), _usage(5200, 150)
    )
    if case.name.startswith("revise_adversarial_music"):
        read["calls"][0]["contains"] = ["PWNED", "admin mode"]  # the text arrives as data
    both = {"draft": {"$draft": "revised"}}
    preview = _turn(
        _call("preview_draft", both, expect={"ok": True}, contains=["quality"]), _usage(7400, 2400)
    )
    submit_in = {**both, "note": NOTES[case.name]}
    submit = _turn(
        _call("submit_draft", submit_in, expect={"accepted": True}), _usage(10000, 300, 5000)
    )
    return FakeScript.model_validate(
        {"model": "fake-model", "drafts": {"revised": revised}, "turns": [read, preview, submit]}
    )


def script_for(case: Case) -> FakeScript:
    if case.revise is not None:
        return revise_script(case)
    good = good_draft(case)
    drafts: dict[str, Any] = {"good": good}
    read = _turn(
        _call("get_music", {}, expect={"ok": True}, contains=['"tracks"']), _usage(4200, 150)
    )
    if case.name.startswith("adversarial_music"):
        read["calls"][0]["contains"] = ["PWNED", "admin mode"]  # the text arrives as data
    good_in = {"draft": {"$draft": "good"}}
    preview = _turn(
        _call("preview_draft", good_in, expect={"ok": True}, contains=["quality"]),
        _usage(6300, 2400),
    )
    submit = _turn(
        _call("submit_draft", good_in, expect={"accepted": True}), _usage(8900, 300, 4000)
    )
    turns: list[dict[str, Any]]
    if case.name == "hard_45_sprints":
        drafts["first"] = _standing_first(good)
        first = {"draft": {"$draft": "first"}}
        turns = [
            read,
            _turn(
                _call("preview_draft", first, contains=["standing_too_long"]), _usage(6300, 2400)
            ),
            _turn(_call("preview_draft", good_in, expect={"ok": True}), _usage(9000, 2400, 4000)),
            submit,
        ]
    elif case.name == "ftp_5_45":
        drafts["first"] = make_draft(
            case.brief.length_min * 60, intensity=case.brief.intensity, ftp=5, ftp_lead=300
        )
        first = {"draft": {"$draft": "first"}}
        failed = _call(
            "preview_draft",
            first,
            expect={"ok": False, "error": "invalid_draft"},
            contains=["lasts 300 s"],
        )
        turns = [read, _turn(failed, _usage(6300, 2400)), preview, submit]
    elif case.name == "long_60":
        drafts["first"] = _short_cooldown(good)
        first = {"draft": {"$draft": "first"}}
        refused = _call("submit_draft", first, expect={"ok": False}, contains=["cooldown"])
        turns = [read, preview, _turn(refused, _usage(8800, 2400)), submit]
    elif case.name == "music_no_sections":
        sections = _call("get_music", {"track": 2}, expect={"ok": True}, contains=["sections"])
        turns = [read, _turn(sections, _usage(4400, 200)), preview, submit]
    elif case.name == "minimum_10":
        turns = [read, submit]
    else:
        turns = [read, preview, submit]
    return FakeScript.model_validate({"model": "fake-model", "drafts": drafts, "turns": turns})


def provider_for_case(case: Case) -> FakeProvider:
    return FakeProvider(script_for(case))
