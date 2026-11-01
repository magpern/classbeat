"""ADR 0017 P4: the rules section of the first user message, the ``rules`` entries of the
``preview_draft`` and ``submit_draft`` results, the provenance ``rules`` and the pin that no stage's
``prompt_version`` moved. Synthetic fixtures only; no model is called (a scripted provider)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import (
    CONFIG,
    MemoryHost,
    analysed,
    brief,
    from_dict,
    good_draft,
)
from classbeat.agent import DesignContext, design_stage, model_input, run_stage
from classbeat.agent.design import DesignResult
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.model_input import MusicData
from classbeat.agent.provider import ToolCall
from classbeat.agent.records import Provenance, ProvenanceRules
from classbeat.agent.revise import ReviseContext, revise_stage
from classbeat.agent.schemas import WorkoutDraft
from classbeat.design import guidance
from classbeat.design.guidance import RuleSet, rule_set_for
from classbeat.design.rules import RATIONALES, default_registry
from classbeat.models.versions import all_prompt_versions

# Pinned on main before P4 (ADR 0017: no tool name, description, schema or system prompt text is
# edited, so every stage's prompt_version is unchanged). A change to any of them fails this test;
# if one is intended it is a prompt change with its own smoke run, not part of the rules.
PINNED = {
    "design": "200aa3e3bdf1c98d0ad132c0f34ec199d893d6d887552e13e994ad39ca9d9618",
    "revise": "d334e6777b995feb4eaa6908e0a207f6bd6019fc73d6ca7071cbcecc5db6a978",
    "pick_pool": "58d36e6a25155e86a70e88b79c845ccd1501f7f2db4c7d28af9f6f78badb5449",
    "choose_songs": "4fb091519ef4af656c3fc32e424bc1b083db5531a7436e8976dc2e0e60a386db",
}
PLANTED = "Zq9 planted brief text"
PLANTED_STYLE = "Zq9 planted style"


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> MusicData:
    return analysed(monkeypatch)


def hard_draft() -> dict[str, Any]:
    """A short, all-out draft: it breaks most rules of a 30 minute interval profile."""

    def iv(sec: int, zone: int, standing: bool) -> dict[str, Any]:
        return {"sec": sec, "zone": zone, "rpm": [70, 75], "standing": standing}

    return {
        "name": "Invented all-out",
        "description": "Everything hard, from the first minute.",
        "segments": [
            {"name": "Go", "kind": "main", "intervals": [iv(600, 7, True), iv(600, 6, True)]},
            {"name": "Cool", "kind": "cooldown", "intervals": [iv(60, 1, False)]},
        ],
    }


def call(stage: Any, name: str, args: Any) -> dict[str, Any]:
    done = dispatch(stage, ToolCall("c1", name, args))
    return json.loads(done.result.content)


# --- prompt_version pin ---------------------------------------------------------------------


def test_every_stages_prompt_version_is_the_pinned_one() -> None:
    assert all_prompt_versions() == PINNED


def test_a_rule_set_in_the_context_does_not_change_the_prompt_version(data: MusicData) -> None:
    rules = rule_set_for(brief())
    assert rules is not None
    ctx = DesignContext.build(brief(), data, rules)
    assert design_stage(ctx).prompt_version == PINNED["design"]
    rev = ReviseContext.build(
        brief(),
        data,
        WorkoutDraft.model_validate(good_draft()),
        (),
        "Harder please",
        music_changed=False,
        rules=rules,
    )
    assert revise_stage(rev).prompt_version == PINNED["revise"]


# --- no profile: byte-identical -------------------------------------------------------------


def test_no_profile_gives_byte_identical_input_and_results(data: MusicData) -> None:
    b = brief(length_min=20)  # no profile within 3 minutes of 30, 45 or 60
    assert rule_set_for(b) is None
    ctx = DesignContext.build(b, data, None)
    expected = model_input.FIRST_MESSAGE + json.dumps(
        {"brief": model_input.brief_for_model(b), "music": ctx.summary},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert ctx.first_message() == expected
    stage = design_stage(ctx)
    assert "rules" not in call(stage, "preview_draft", {"draft": good_draft()})
    assert "rules" not in call(stage, "submit_draft", {"draft": good_draft()})
    base = WorkoutDraft.model_validate(good_draft())
    rev = ReviseContext.build(b, data, base, ("a",), "b", music_changed=False)
    plain = model_input.revise_message(
        b, rev.summary, base.model_dump(mode="json", exclude_none=True), ("a",), "b",
        music_changed=False,
    )  # fmt: skip
    assert rev.first_message() == plain
    assert "Patterns for" not in plain


# --- the section ----------------------------------------------------------------------------


def test_the_section_is_in_the_first_message_before_the_payload(data: MusicData) -> None:
    rules = rule_set_for(brief())
    assert rules is not None
    message = DesignContext.build(brief(), data, rules).first_message()
    section = rules.section()
    assert message.startswith(model_input.FIRST_MESSAGE + section + "\n\n{")
    assert message.count(guidance.HEADER) == 1


def test_every_shipped_profile_has_a_capped_section_made_of_our_words() -> None:
    reg = default_registry()
    allowed = {s.rstrip(".") for s in RATIONALES.values()}
    for pid, profile in reg.profiles.items():
        rules = RuleSet(profile, reg.digests[pid], brief())
        section = rules.section()
        if not profile.rules:
            assert section == ""
            continue
        lines = section.split("\n")
        assert len(lines) <= 8, pid
        assert len(section) <= 1200, pid
        assert lines[0] == guidance.HEADER
        assert lines[-1] == guidance.STANDING
        for line in lines[1:-1]:
            assert line.startswith("- ")
            assert line.endswith(f"; {guidance.USUALLY}).")
            assert line[2 : line.index(" (")] in allowed


def test_no_brief_or_music_text_reaches_the_section(data: MusicData) -> None:
    b = brief(text=PLANTED, style=PLANTED_STYLE)
    rules = rule_set_for(b)
    assert rules is not None
    section = rules.section()
    for value in (PLANTED, PLANTED_STYLE, "Invented Tuesday Mix", "Invented Song"):
        assert value not in section
    spec = good_draft()
    result = call(
        design_stage(DesignContext.build(b, data, rules)), "preview_draft", {"draft": spec}
    )
    assert PLANTED not in json.dumps(result["rules"])
    assert PLANTED_STYLE not in json.dumps(result["rules"])


def test_a_dismissed_rule_is_left_out_of_the_section_and_the_hits(data: MusicData) -> None:
    full = rule_set_for(brief())
    assert full is not None
    ids = [r.rule for r in full.profile.rules]
    some = rule_set_for(brief(), ids[:2])
    assert some is not None
    spec = fitted(data, brief())
    assert {h["rule"] for h in full.hits(spec)} & set(ids[:2])  # the fixture does hit them
    assert not {h["rule"] for h in some.hits(spec)} & set(ids[:2])
    first = RATIONALES[full.profile.rules[0].rationale_id].rstrip(".")
    assert first in full.section()
    assert first not in some.section()
    assert rule_set_for(brief(), ids) is None  # nothing left to show


# --- the tool results -----------------------------------------------------------------------


def test_hits_are_in_both_results_capped_and_never_block_submit(data: MusicData) -> None:
    rules = rule_set_for(brief(sprints=0, intensity="moderate"))
    assert rules is not None
    stage = design_stage(DesignContext.build(brief(sprints=0), data, rules))
    preview = call(stage, "preview_draft", {"draft": hard_draft()})
    assert 1 <= len(preview["rules"]) <= 5
    assert set(preview["rules"][0]) == {
        "rule", "severity", "value", "bounds", "explained_by_brief", "note",
    }  # fmt: skip
    assert preview["rules"][0]["severity"] == "warn"
    done = dispatch(stage, ToolCall("c1", "submit_draft", {"draft": hard_draft()}))
    assert not done.result.is_error
    body = json.loads(done.result.content)
    assert body["accepted"] is True
    assert body["rules"] == preview["rules"]
    assert isinstance(done.output, DesignResult)


def test_a_draft_with_no_hit_adds_an_empty_list(data: MusicData) -> None:
    rules = rule_set_for(brief())
    assert rules is not None
    only = RuleSet(rules.profile.model_copy(update={"rules": []}), rules.digest, brief())
    stage = design_stage(DesignContext.build(brief(), data, only))
    assert call(stage, "preview_draft", {"draft": good_draft()})["rules"] == []


def fitted(data: MusicData, b: Any) -> dict[str, Any]:
    stage = design_stage(DesignContext.build(b, data))
    done = dispatch(stage, ToolCall("c", "submit_draft", {"draft": hard_draft()}))
    assert isinstance(done.output, DesignResult)
    return done.output.spec


def test_the_brief_marks_a_hit_explained_and_a_chosen_class_type_does_not(data: MusicData) -> None:
    def hits_for(b: Any) -> list[dict[str, Any]]:
        rules = rule_set_for(b)
        assert rules is not None
        return rules.hits(fitted(data, b))

    stated = hits_for(brief(sprints=0, text="Hard from the first minute"))
    assert stated
    assert all(h["explained_by_brief"] and h["severity"] == "info" for h in stated)
    # a class type the user chose, and nothing else in the brief, explains nothing
    chosen = hits_for(brief(sprints=0, class_type="interval"))
    assert chosen
    assert not any(h["explained_by_brief"] for h in chosen)


def test_the_revise_request_is_not_read_by_the_engine(data: MusicData) -> None:
    rules = rule_set_for(brief(sprints=0))
    assert rules is not None
    base = WorkoutDraft.model_validate(good_draft())

    def result(request: str) -> Any:
        ctx = ReviseContext.build(
            brief(sprints=0), data, base, (), request, music_changed=False, rules=rules
        )
        return call(revise_stage(ctx), "preview_draft", {"draft": hard_draft()})["rules"]

    assert result("make it harder") == result("make it easier")
    assert not any(h["explained_by_brief"] for h in result("make it harder"))


def test_the_revise_message_has_the_section_without_the_dismissed(data: MusicData) -> None:
    full = rule_set_for(brief())
    assert full is not None
    first = full.profile.rules[0].rule
    less = rule_set_for(brief(), [first])
    assert less is not None
    base = WorkoutDraft.model_validate(good_draft())
    ctx = ReviseContext.build(
        brief(), data, base, (), "Harder please", music_changed=False, rules=less
    )
    message = ctx.first_message()
    assert message.count(guidance.HEADER) == 1
    assert message.index(guidance.HEADER) < message.index('{"brief"')
    assert RATIONALES[full.profile.rules[0].rationale_id].rstrip(".") not in message
    assert "Harder please" not in less.section()


# --- provenance -----------------------------------------------------------------------------


def test_provenance_records_the_profile_the_model_saw() -> None:
    rules = rule_set_for(brief())
    assert rules is not None
    reg = default_registry()
    seen = rules.provenance()
    assert seen == {
        "profile": "cycling.interval.30",
        "version": reg.profiles["cycling.interval.30"].version,
        "digest": reg.digests["cycling.interval.30"],
    }
    assert ProvenanceRules.model_validate(seen).profile == seen["profile"]
    with pytest.raises(ValidationError):
        ProvenanceRules.model_validate({**seen, "extra": 1})
    with pytest.raises(ValidationError):
        ProvenanceRules.model_validate({**seen, "digest": "nope"})
    assert "rules" in Provenance.model_fields


# --- replay ---------------------------------------------------------------------------------


async def test_a_scripted_run_shows_the_section_and_the_hits(data: MusicData) -> None:
    rules = rule_set_for(brief(sprints=0))
    assert rules is not None
    ctx = DesignContext.build(brief(sprints=0), data, rules)
    script = {
        "drafts": {"hard": hard_draft()},
        "turns": [
            {"calls": [{"name": "preview_draft", "input": {"draft": {"$draft": "hard"}}}]},
            {"calls": [{"name": "submit_draft", "input": {"draft": {"$draft": "hard"}}}]},
        ],
    }
    fake = from_dict(script)
    outcome = await run_stage(MemoryHost(), fake, design_stage(ctx), ctx.first_message(), CONFIG)
    assert isinstance(outcome.output, DesignResult)  # every rule violated, still accepted
    [sent] = fake.sent
    assert guidance.HEADER in sent.spec.first_message
    assert sent.spec.system.count(guidance.HEADER) == 0
    assert all(guidance.HEADER not in t.description for t in sent.spec.tools)
    [(results, _)] = sent.turns[1:2]
    assert json.loads(results[0].content)["rules"]
    assert outcome.info.prompt_version == PINNED["design"]


def test_the_revise_submit_draft_returns_the_hits_and_accepts_the_draft(data: MusicData) -> None:
    rules = rule_set_for(brief(sprints=0))
    assert rules is not None
    base = WorkoutDraft.model_validate(good_draft())
    ctx = ReviseContext.build(
        brief(sprints=0), data, base, (), "Harder", music_changed=False, rules=rules
    )
    stage = revise_stage(ctx)
    done = dispatch(stage, ToolCall("c1", "submit_draft", {"draft": hard_draft(), "note": ""}))
    assert not done.result.is_error
    body = json.loads(done.result.content)
    assert body["accepted"] is True
    assert 1 <= len(body["rules"]) <= 5
    assert body["rules"] == call(stage, "preview_draft", {"draft": hard_draft()})["rules"]
    no_profile = ReviseContext.build(brief(length_min=20), data, base, (), "x", music_changed=False)
    plain = dispatch(
        revise_stage(no_profile), ToolCall("c2", "submit_draft", {"draft": hard_draft()})
    )
    assert "rules" not in json.loads(plain.result.content)
