"""#1372 (ADR 0036 amendment B6): **Re-plan the other songs** is a ``studio_revise`` that
carries the user's ``choices`` (the field the consent screen's ``createReplanJob`` posts). Admit
reads them and checks every position against the base's approved list: anything else is
``invalid_input`` and nothing is created (no job, no chat message, no credit). An admitted
re-plan states the choices to the model first, by index, and stores an ordinary revise
(``origin: revise_draft``, a new proposal). Fake gateway, fake spotdown and a recorded
``FakeProvider``: no network."""

import copy
from typing import Any
from uuid import UUID

import pytest

from classbeat.agent.studio_replan import HEAD, choices_section
from classbeat.studio.mix_choice_model import on_list
from songtools import new_key
from spotdownfake import spotdown
from studiotools import StudioEnv, StudioRider
from test_swedebeat_studio_design import (
    MIX_8,
    analysed,
    design_script,
    designed,
    held_credits,
    run_kind,
)
from test_swedebeat_studio_songs import _sent  # pyright: ignore[reportPrivateUsage]

__all__ = ["analysed", "spotdown"]  # the fixtures
pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")

REPLAN = "Re-plan the other songs around my choices"


async def replan(
    rider: StudioRider, class_id: UUID, base: UUID, choices: object, key: str | None = None
) -> Any:
    body = {
        "kind": "studio_revise",
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "request": REPLAN,
            "swedebeat_connection_id": str(rider.swedebeat_connection_id),
            "choices": choices,
        },
    }
    return await rider.browser.post("/api/jobs", body, {"Idempotency-Key": key or new_key()})


def counts(studio: StudioEnv, owner: UUID) -> tuple[int, int, int]:
    """The user's jobs, chat messages and credit ledger rows."""
    sql = studio.songs.sql
    return (
        sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one(),
        sql("SELECT count(*) FROM class_messages WHERE owner_id = :o", o=owner).scalar_one(),
        sql("SELECT count(*) FROM credit_ledger WHERE owner_id = :o", o=owner).scalar_one(),
    )


BAD: list[object] = [
    {"dj_mix": False, "blend": False, "trim": False, "keep_whole": [0]},  # whole songs: no set
    {"dj_mix": True, "blend": True, "trim": True, "keep_whole": [8]},  # the list has 8 songs
    {"dj_mix": True, "blend": True, "trim": True, "hard_cut_out": [2, 2]},
    {"dj_mix": True, "blend": True, "trim": True, "reference": "ref-1"},
    {"dj_mix": True, "trim": True},
    {},
]


async def test_invalid_choices_are_refused_at_admit_and_nothing_is_created(
    studio: StudioEnv,
) -> None:
    rider, class_id, _, current = await designed(studio)
    before = counts(studio, rider.user_id)
    for raw in BAD:
        refused = await replan(rider, class_id, current.current_revision_id, raw)
        assert refused.status_code == 409, (raw, refused.text)
        assert refused.json()["detail"]["reason"] == "invalid_input", raw
        assert counts(studio, rider.user_id) == before, raw
    for raw in ("whole", [0, 1], 3):  # not an object: the input does not read
        refused = await replan(rider, class_id, current.current_revision_id, raw)
        assert refused.status_code in (409, 422), (raw, refused.text)
        assert counts(studio, rider.user_id) == before, raw


async def test_a_replan_states_the_choices_first_and_stores_an_ordinary_revise(
    studio: StudioEnv,
) -> None:
    rider, class_id, _, current = await designed(studio)
    base = studio.songs.revision(current.current_revision_id)
    raw = {"dj_mix": True, "blend": False, "trim": True, "keep_whole": [3, 1], "hard_cut_in": [0]}
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0
    studio.songs.script = design_script(cut, note="Re-planned the other songs.")
    response = await replan(rider, class_id, base.id, raw)
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    sent = _sent(studio)
    section = choices_section(on_list(raw, 8))
    assert section in sent  # first in the revise's message, by index
    assert "- keep whole (once, from 0 to its length): tracks 1, 3" in sent
    assert sent.count(HEAD) == 1
    new_id = studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    new = studio.songs.revision(new_id)
    assert new.parent_id == base.id
    assert new.provenance["origin"] == "revise_draft"  # a proposal of its own (B4)
    assert new.music["mix_spec"] != base.music["mix_spec"]
    assert held_credits(studio, rider.user_id).count("studio_revise:none") == 1


async def test_a_chat_revise_without_choices_states_none(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0
    studio.songs.script = design_script(cut, note="Cut.")
    body = {
        "kind": "studio_revise",
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(current.current_revision_id),
            "request": "Cut the last song",
            "swedebeat_connection_id": str(rider.swedebeat_connection_id),
        },
    }
    response = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    assert HEAD not in _sent(studio)


async def test_choices_the_admit_checked_but_the_stored_input_breaks_fail_internal(
    studio: StudioEnv,
) -> None:
    rider, class_id, _, current = await designed(studio)
    studio.songs.script = design_script(MIX_8)
    raw = {"dj_mix": True, "blend": True, "trim": True}
    response = await replan(rider, class_id, current.current_revision_id, raw)
    assert response.status_code == 201, response.text
    job_id = UUID(response.json()["id"])
    studio.songs.sql(
        "UPDATE jobs SET input = jsonb_set(input, '{choices}', CAST(:c AS jsonb)) WHERE id = :id",
        c='{"dj_mix": true, "blend": true, "trim": true, "keep_whole": [99]}',
        id=job_id,
    )
    row = await run_kind(studio, job_id)
    assert (row.state, row.error_category) == ("failed", "internal")


async def test_another_user_cannot_replan_a_class_with_choices(studio: StudioEnv) -> None:
    """User B names user A's class and base revision with valid choices: refused, nothing is
    created for either user and no model call is made (owner isolation, ADR 0002)."""
    rider, class_id, _, current = await designed(studio)
    other = await studio.rider()
    valid = {"dj_mix": True, "blend": True, "trim": True, "keep_whole": [1]}
    owners = (rider.user_id, other.user_id)
    before = [counts(studio, owner) for owner in owners]
    sent_before = _sent(studio)
    refused = await replan(other, class_id, current.current_revision_id, valid)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (404, "class_not_found")
    assert [counts(studio, owner) for owner in owners] == before
    assert _sent(studio) == sent_before  # nothing about A's class reached the model
    # The body is valid (a 422 or 409 could not stand in for the ownership check): the owner's
    # own re-plan with the very same choices is accepted.
    accepted = await replan(rider, class_id, current.current_revision_id, valid)
    assert accepted.status_code == 201, accepted.text
