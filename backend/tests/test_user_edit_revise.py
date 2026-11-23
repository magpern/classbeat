"""#1391 (ADR 0042 sections 3 and 8): a studio revise (an AI regeneration) of an edited draft. The
message states the instructor's locks and overrides as fixed; an override (a zone change) survives
the regeneration with no lock, its ``ai_value_at_set`` the model's new value; a draft that breaks a
lock is refused ``overlay_violated``, a correction round; the new revision stores the reconciled
overlay under its hash; a tampered overlay is refused. On the fakes (the SwedeBeat gateway,
spotdown's ``exact`` maps, ``FakeProvider``) and Postgres as ``classbeat_app``; never the
network."""

import copy
import json
from typing import Any
from uuid import UUID

import pytest

from agentfake import good_draft
from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import blocks
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    MIX_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    design_script,
    designed,
    revise,
    run_kind,
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)
from test_swedebeat_studio_songs import _sent  # pyright: ignore[reportPrivateUsage]
from test_user_edit_routes import current_id, edit, overlay_of

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default")

CUT = copy.deepcopy(MIX_8)
CUT["tracks"][-1]["end_sec"] = 230.0  # after the workout's end: the model's workout is the same


def zone_at(studio: StudioEnv, revision_id: UUID, at: int) -> Any:
    return blocks(studio.songs.revision(revision_id).content)[at].interval.get("zone")


async def edited(studio: StudioEnv, op: dict[str, Any]) -> tuple[Any, UUID, UUID]:
    rider, class_id, _list, proposal = await designed(studio)
    answer = await edit(rider, class_id, proposal.current_revision_id, op)
    assert answer.status_code == 200, answer.text
    return rider, class_id, UUID(answer.json()["revision_id"])


async def revised(studio: StudioEnv, rider: Any, class_id: UUID, base: UUID) -> Any:
    response = await revise(rider, class_id, base, "Cut the last song shorter")
    assert response.status_code == 201, response.text
    return await run_kind(studio, UUID(response.json()["id"]))


async def test_a_zone_change_survives_the_regeneration_with_no_lock(studio: StudioEnv) -> None:
    model_zone = good_draft()["segments"][0]["intervals"][1]["zone"]
    mine = 4 if model_zone != 4 else 5
    rider, class_id, base = await edited(
        studio, {"op": "set", "block": 1, "field": "zone", "value": mine}
    )
    studio.songs.script = design_script(CUT, note="Cut the last song.")
    row = await revised(studio, rider, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)
    new = current_id(studio, class_id)
    stored = studio.songs.revision(new)
    assert (stored.parent_id, stored.provenance["origin"]) == (base, "revise_draft")
    assert zone_at(studio, new, 1) == mine  # the model sent its own zone; the user's is kept
    overlay = overlay_of(studio, new)
    (record,) = overlay["records"]
    assert (record["field"], record["value"], record["ai_value_at_set"]) == (
        "zone",
        mine,
        model_zone,
    )
    assert bytes(stored.content_hash) == revision_hash(
        stored.content, mix_spec=stored.music["mix_spec"], overlay=overlay
    )
    sent = _sent(studio)
    assert "The instructor's own edits are fixed" in sent
    assert f"set zone = {mine}" in sent


async def test_a_draft_breaking_a_lock_is_a_correction_round(studio: StudioEnv) -> None:
    rider, class_id, base = await edited(studio, {"op": "lock", "block": 2, "scope": "all"})
    script = design_script(CUT, note="Cut the last song.")
    bad = good_draft()
    bad["segments"][1]["intervals"][0]["zone"] = 5  # block 2: the locked one
    script["drafts"]["bad"] = bad
    submit = script["turns"][-1]["calls"][0]
    breach = {**submit, "input": {**submit["input"], "draft": {"$draft": "bad"}}}
    breach["expect"] = {"ok": False, "error": "overlay_violated"}
    script["turns"].insert(-1, {"calls": [breach]})
    studio.songs.script = script
    row = await revised(studio, rider, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)  # the second submit kept it
    new = current_id(studio, class_id)
    assert zone_at(studio, new, 2) == zone_at(studio, base, 2)
    assert [r["type"] for r in overlay_of(studio, new)["records"]] == ["lock"]
    assert "locked by the instructor" in _sent(studio)


async def test_a_tampered_overlay_fails_the_revise(studio: StudioEnv) -> None:
    rider, class_id, base = await edited(studio, {"op": "lock", "block": 2, "scope": "all"})
    overlay = overlay_of(studio, base)
    overlay["records"][0]["scope"] = "timing"
    studio.songs.sql(
        "UPDATE class_revisions SET edit_overlay = CAST(:o AS jsonb) WHERE id = :r",
        o=json.dumps(overlay),
        r=base,
    )
    studio.songs.script = design_script(CUT, note="Cut the last song.")
    row = await revised(studio, rider, class_id, base)  # admitted on the stored hash
    assert (row.state, row.error_category) == ("failed", "internal")  # the run recomputes it
    assert current_id(studio, class_id) == base
