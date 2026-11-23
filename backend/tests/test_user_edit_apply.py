"""#1391 (ADR 0042 section 8): **Apply these choices** on an edited studio draft carries its
overlay. ``apply_mix_choices`` remaps the edited workout onto the derived spec, re-applies the
overlay by its fixed order and stores it with the new draft, its hash covering it; a timing-locked
block the remap moves is ``locked_block_conflict`` and stores nothing. On the fakes (the SwedeBeat
gateway, spotdown's ``exact`` maps, ``FakeProvider``) and Postgres as ``classbeat_app``; never the
network."""

from typing import Any
from uuid import UUID

import pytest

from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import blocks
from mixapplytools import NO_MODEL, WHOLE, applied, create, current, designed
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)
from test_user_edit_routes import edit, overlay_of, revision_count

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default")


def zone_at(studio: StudioEnv, revision_id: UUID, at: int) -> Any:
    return blocks(studio.songs.revision(revision_id).content)[at].interval.get("zone")


async def test_apply_keeps_the_users_zone_and_stores_the_overlay(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    zone = 2 if zone_at(studio, proposal, 1) != 2 else 3
    answer = await edit(
        rider, class_id, proposal, {"op": "set", "block": 1, "field": "zone", "value": zone}
    )
    assert answer.status_code == 200, answer.text
    edited = UUID(answer.json()["revision_id"])
    stored = await applied(studio, rider, class_id, WHOLE)
    new = current(studio, class_id)
    assert new != edited
    assert stored.provenance["origin"] == "mix_choices"
    assert stored.parent_id == edited
    assert stored.music["mix_spec"] != studio.songs.revision(edited).music["mix_spec"]
    assert zone_at(studio, new, 1) == zone  # the user's edit survives Apply
    overlay = overlay_of(studio, new)
    assert [(r["type"], r["field"], r["value"]) for r in overlay["records"]] == [
        ("override", "zone", zone)
    ]
    assert bytes(stored.content_hash) == revision_hash(
        stored.content, mix_spec=stored.music["mix_spec"], overlay=overlay
    )


async def test_apply_on_a_draft_without_an_overlay_stores_none(studio: StudioEnv) -> None:
    rider, class_id, _proposal = await designed(studio)
    stored = await applied(studio, rider, class_id, WHOLE)
    assert overlay_of(studio, current(studio, class_id)) is None
    assert bytes(stored.content_hash) == revision_hash(
        stored.content, mix_spec=stored.music["mix_spec"]
    )


async def test_apply_moving_a_timing_locked_block_is_refused(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    late = len(blocks(studio.songs.revision(proposal).content)) - 2  # the remap moves it
    answer = await edit(rider, class_id, proposal, {"op": "lock", "block": late, "scope": "timing"})
    assert answer.status_code == 200, answer.text
    count = revision_count(studio, class_id)
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, WHOLE)
    assert (created.status_code, created.json()["detail"]["reason"]) == (
        409,
        "locked_block_conflict",
    )
    assert revision_count(studio, class_id) == count
