"""#1391 (ADR 0042 sections 3, 5, 6 and 8): an edited studio draft through the approvals. B9 admits
a ``user_edit`` draft (its choices its nearest other draft's), with a workout edit and with a cut
pin; an approval with a stale ``base_revision_id`` is ``stale_revision``; a tampered overlay is
refused where the stored hash is checked; a pin edit that breaks the class-length rule stores
nothing. On the fakes (the SwedeBeat gateway, spotdown's ``exact`` maps, the model) and Postgres
as ``classbeat_app``; never the network."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import user_edit
from classbeat.plan_gates import PlanGates
from studiooutputs import (
    choice,
    outputs_on,  # noqa: F401 - fixture
)
from studiotools import StudioEnv
from test_studio_mix_not_applied import asked_since, post
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)
from test_user_edit_routes import current_id, edit, overlay_of, revision_count

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")

MIX = ("swedebeat_playlist", "swedebeat_mix")


def current(studio: StudioEnv, class_id: UUID) -> Any:
    return studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).one()


def end_of(studio: StudioEnv, revision_id: UUID, piece: int) -> float:
    return float(studio.songs.revision(revision_id).music["mix_spec"]["tracks"][piece]["end_sec"])


async def test_a_workout_edit_of_a_proposal_is_admitted_by_b9(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    answer = await edit(
        rider, class_id, proposal.current_revision_id, {"op": "lock", "block": 0, "scope": "all"}
    )
    assert answer.status_code == 200, answer.text
    chosen = await post(rider, class_id, choice(current(studio, class_id), studio, *MIX))
    assert chosen.status_code == 201, chosen.text


async def test_a_pinned_user_edit_is_admitted_and_its_pins_are_checked(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    at = round(end_of(studio, base, 0) - 15.0, 2)
    pinned = await edit(
        rider,
        class_id,
        base,
        {"op": "pin_set", "piece": 0, "edge": "end", "requested_sec": at, "mode": "exact"},
    )
    assert pinned.status_code == 200, pinned.text
    new = UUID(pinned.json()["revision_id"])
    stored = studio.songs.revision(new)
    assert stored.music["mix_spec"]["tracks"][0]["end_pin"]["requested_sec"] == at
    (record,) = overlay_of(studio, new)["records"]
    sha = {a["reference"]: a["sha256"] for a in stored.music["analyses"]}
    assert record["set_against"] == sha[record["reference"]]
    assert record["confirmed"] is False
    chosen = await post(rider, class_id, choice(current(studio, class_id), studio, *MIX))
    assert chosen.status_code == 201, chosen.text


async def test_a_pin_edit_breaking_the_class_length_stores_nothing(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    count = revision_count(studio, class_id)
    answer = await edit(
        rider,
        class_id,
        base,
        {"op": "pin_set", "piece": 0, "edge": "end", "requested_sec": 1.0, "mode": "exact"},
    )
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "window_too_short"  # step 1, ``checked``
    assert revision_count(studio, class_id) == count
    assert current_id(studio, class_id) == base


async def test_an_approval_with_a_stale_base_is_refused(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    answer = await edit(rider, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert answer.status_code == 200, answer.text
    body = {**choice(current(studio, class_id), studio, *MIX), "base_revision_id": str(base)}
    before = len(studio.gateway.requests)
    refused = await post(rider, class_id, body)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "stale_revision")
    assert asked_since(studio, before) == []
    fresh = {**body, "base_revision_id": answer.json()["revision_id"]}
    assert (await post(rider, class_id, fresh)).status_code == 201


async def test_a_tampered_overlay_is_refused_by_the_outputs_and_the_editor(
    studio: StudioEnv,
) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    answer = await edit(
        rider, class_id, proposal.current_revision_id, {"op": "lock", "block": 0, "scope": "all"}
    )
    new = UUID(answer.json()["revision_id"])
    overlay = overlay_of(studio, new)
    overlay["records"][0]["scope"] = "timing"
    studio.songs.sql(
        "UPDATE class_revisions SET edit_overlay = CAST(:o AS jsonb) WHERE id = :r",
        o=json.dumps(overlay),
        r=new,
    )
    chosen = await post(rider, class_id, choice(current(studio, class_id), studio, *MIX))
    assert (chosen.status_code, chosen.json()["detail"]["reason"]) == (500, "internal")
    again = await edit(rider, class_id, new, {"op": "unlock", "block": 0})
    assert again.status_code == 500
    assert again.json()["detail"] == {"reason": "internal"}


async def test_pin_edits_are_held_to_the_class_length_and_the_first_stays(
    studio: StudioEnv,
) -> None:
    """Step 2: each pin shortens the mix; the edit past the 10 % rule stores nothing."""
    rider, class_id, list_id, proposal = await designed(studio)
    target = studio.songs.revision(list_id).content["target_sec"]
    base = proposal.current_revision_id
    reasons: list[str] = []
    for piece in range(8):
        at = round(end_of(studio, base, piece) - 100.0, 2)
        op = {"op": "pin_set", "piece": piece, "edge": "end", "requested_sec": at, "mode": "exact"}
        count = revision_count(studio, class_id)
        answer = await edit(rider, class_id, base, op)
        if answer.status_code != 200:
            reasons.append(answer.json()["detail"]["reason"])
            assert revision_count(studio, class_id) == count
            assert current_id(studio, class_id) == base
            break
        base = UUID(answer.json()["revision_id"])
    assert reasons == ["class_length_out_of_tolerance"], (target, reasons)


@pytest.mark.parametrize(
    "op",
    [
        {"op": "pin_set", "piece": 0, "edge": "end", "requested_sec": 200.0, "mode": "exact"},
        {"op": "lock", "block": 0, "scope": "all"},
    ],
)
async def test_an_edit_past_the_current_plan_stores_nothing(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, op: dict[str, Any]
) -> None:
    """Step 4 with the user's **current** plan: a class longer than it allows is refused."""
    short = PlanGates(max_class_minutes=10, games=True, races=True, ftp_tests=True)

    async def gates(*_: object) -> tuple[None, PlanGates]:
        return None, short

    monkeypatch.setattr(user_edit, "current_gates", gates)
    rider, class_id, _list, proposal = await designed(studio)
    count = revision_count(studio, class_id)
    answer = await edit(rider, class_id, proposal.current_revision_id, op)
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "plan_class_too_long")
    assert revision_count(studio, class_id) == count
