"""ADR 0017 P4 at the job level (Postgres as ``classbeat_app``, the fake MOWL, ``FakeProvider``):
the profile the model saw is in the stored revision's provenance on all four job paths, and the
ids in ``classes.dismissed_rules`` (read by ``chat.load_base``) reach the rule set of a revise and
of a revise-songs, so a dismissed rule is in neither the first message nor the tool results."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.agent import model_input
from classbeat.agent.providers.fake import FakeProvider
from classbeat.agent.schemas import Brief, WorkoutDraft
from classbeat.design import guidance
from classbeat.design.guidance import RuleSet
from classbeat.design.rules import RATIONALES, default_registry
from revisesongtools import changed_approved, picked
from revisetools import ReviseEnv
from songtools import SongEnv

PROFILE = "cycling.interval.30"  # the brief of both worlds: 30 minutes with sprints


def expected() -> dict[str, Any]:
    reg = default_registry()
    return {
        "profile": PROFILE,
        "version": reg.profiles[PROFILE].version,
        "digest": reg.digests[PROFILE],
    }


def rule_set() -> RuleSet:
    reg = default_registry()
    return RuleSet(reg.profiles[PROFILE], reg.digests[PROFILE], Brief(length_min=30))


def base_spec(content: dict[str, Any]) -> dict[str, Any]:
    return WorkoutDraft.model_validate(content).to_spec()


def to_dismiss(content: dict[str, Any]) -> list[str]:
    """The rules the stored base draft hits, so the dismissal has something to remove; at least
    two rules stay, so a section is still shown."""
    hit = [h["rule"] for h in rule_set().hits(base_spec(content))]
    assert hit, "the fixture draft must hit a rule or the test proves nothing"
    keep = [r.rule for r in rule_set().profile.rules if r.rule not in hit]
    assert len(keep) >= 2
    return hit


def preview_hits(fake: FakeProvider) -> list[str]:
    """The rule ids in every ``preview_draft`` result the model was sent."""
    out: list[str] = []
    for results, _ in fake.sent[0].turns:
        for r in results:
            body = json.loads(r.content)
            if "timeline" in body:
                assert "rules" in body
                out += [h["rule"] for h in body["rules"]]
    return out


def sentences(rule_ids: list[str]) -> list[str]:
    by_id = {r.rule: r for r in rule_set().profile.rules}
    return [RATIONALES[by_id[i].rationale_id].rstrip(".") for i in rule_ids]


def dismiss(env: Any, class_id: UUID, rule_ids: list[str]) -> None:
    env.sql(
        "UPDATE classes SET dismissed_rules = CAST(:r AS jsonb) WHERE id = :id",
        r=json.dumps(rule_ids),
        id=class_id,
    )


@pytest.mark.usefixtures("jenv")
async def test_design_and_revise_record_the_profile_and_revise_drops_dismissed_rules(
    renv: ReviseEnv,
) -> None:
    class_id, base = await renv.design()
    first = renv.revisions(class_id)[0]
    assert first.provenance["rules"] == expected()  # design_draft
    # Nothing dismissed: the section and the hits are there.
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    [_, revised] = renv.revisions(class_id)
    assert revised.provenance["rules"] == expected()  # revise_draft
    [_, fake] = renv.providers
    assert guidance.HEADER in fake.sent[0].spec.first_message
    seen = preview_hits(fake)
    assert seen
    # Dismiss through the real column and revise again from the new current revision.
    dismissed = to_dismiss(first.content)
    dismiss(renv, class_id, dismissed)
    job_id, _ = await renv.revise(class_id, revised.id, "revise_second", "Make the end harder.")
    assert renv.denv.ops.row(job_id).state == "completed"
    fake = renv.providers[-1]
    message = fake.sent[0].spec.first_message
    for sentence in sentences(dismissed):
        assert sentence not in message
    assert guidance.HEADER in message
    assert message.index(guidance.HEADER) < message.index('{"brief"')
    assert not set(preview_hits(fake)) & set(dismissed)
    last = renv.revisions(class_id)[-1]
    assert last.provenance["rules"] == expected()


@pytest.mark.usefixtures("songenv")
async def test_design_with_songs_and_revise_songs_record_the_profile_and_drop_dismissed(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    designed = songenv.sql("SELECT * FROM class_revisions WHERE id = :i", i=base).one()
    assert designed.provenance["origin"] == "design_with_songs"
    assert designed.provenance["rules"] == expected()
    dismissed = to_dismiss(designed.content)
    dismiss(songenv, class_id, dismissed)
    job_id, _ = await changed_approved(songenv, rider, class_id, base)
    row = await songenv.run(job_id)
    assert row.state == "completed", (row.state, row.error_category)
    made = songenv.sql(
        "SELECT * FROM class_revisions WHERE class_id = :c ORDER BY number DESC LIMIT 1",
        c=class_id,
    ).one()
    assert made.provenance["origin"] == "revise_songs"
    assert made.provenance["rules"] == expected()
    fake = songenv.providers[-1]
    message = fake.sent[0].spec.first_message
    assert guidance.HEADER in message
    for sentence in sentences(dismissed):
        assert sentence not in message
    assert not set(preview_hits(fake)) & set(dismissed)
    assert model_input.REVISE_MESSAGE in message
