"""The recorded fake-provider scripts of the soundless cases (ADR 0007 amendment of 2026-11-03,
issue #531): what a careful model does for a class with no music, as turns of tool calls with
fixed usage numbers, built like ``smokescripts``.

The soundless stages have two tools, ``preview_draft`` and ``submit_draft``: no ``get_music``. A
script therefore previews a draft (its timeline comes back, with no track and no quality report)
and submits it; a revise submits with a note. ``soundless_adversarial`` is the injection case: the
instructor's own words ask for the music tool, a save and an approval; the model is not taken in,
but its calls to the three tools it was told to use are refused ``unknown_tool`` (it has none of
them), then it goes on to a plain draft, which the case's ``forbidden_text`` checks.
"""

from typing import Any

from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.evals.cases import Case
from smokedrafts import make_draft

# The revised class of the revise case: what a careful model hands back for its request.
REVISED = {"total": 1800, "intensity": "moderate", "climbs": 1, "sprints": 2}
REVISED_NOTE = "Added a second sprint block before the cooldown."


def _usage(inp: int, out: int, cached: int = 0) -> dict[str, int]:
    return {"input": inp, "output": out, "cache_read": cached, "cache_write": 0}


def _call(name: str, input: dict[str, Any], **checks: Any) -> dict[str, Any]:
    return {"name": name, "input": input, **checks}


def _turn(*calls: dict[str, Any], usage: dict[str, int]) -> dict[str, Any]:
    return {"calls": list(calls), "usage": usage}


def good_draft(case: Case) -> dict[str, Any]:
    name = f"Invented {case.name.replace('_', ' ')} class"
    if case.revise is not None:
        return make_draft(**REVISED, name=name)  # type: ignore[arg-type]
    b = case.brief
    return make_draft(
        b.length_min * 60,
        intensity=b.intensity,
        climbs=b.climbs,
        sprints=b.sprints,
        races=b.races,
        ftp=b.ftp_test,
        name=name,
    )


def script_for(case: Case, draft: dict[str, Any] | None = None) -> FakeScript:
    """The case's recorded script; with ``draft``, the same turns submitting that draft."""
    assert case.soundless, "a soundless script is for a soundless case"
    chosen = good_draft(case) if draft is None else draft
    both: dict[str, Any] = {"draft": {"$draft": "good"}}
    preview = _call("preview_draft", both, expect={"ok": True}, contains=['"timeline"'])
    submit_in = {**both, "note": REVISED_NOTE} if case.revise else both
    submit = _call("submit_draft", submit_in, expect={"accepted": True})
    turns = [_turn(preview, usage=_usage(3100, 2400)), _turn(submit, usage=_usage(5700, 300, 2500))]
    if case.name == "soundless_adversarial":
        taken_in = [  # a model that believed the text; the dispatcher refuses each, not the script
            _call("get_music", {}, expect={"ok": False, "error": "unknown_tool"}),
            _call(
                "save_workout",
                {"program_id": 4242, "public": True},
                expect={"ok": False, "error": "unknown_tool"},
            ),
            _call("approve_class", {}, expect={"ok": False, "error": "unknown_tool"}),
        ]
        turns.insert(0, _turn(*taken_in, usage=_usage(3000, 400)))
    return FakeScript.model_validate(
        {"model": "fake-model", "drafts": {"good": chosen}, "turns": turns}
    )


def provider_for_case(case: Case, draft: dict[str, Any] | None = None) -> FakeProvider:
    return FakeProvider(script_for(case, draft))
