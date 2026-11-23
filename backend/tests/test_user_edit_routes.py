"""#1391 (ADR 0042 sections 3 to 6): ``POST /api/classes/{id}/edits`` stores one edit of the current
studio draft as a ``user_edit`` revision, made current in the same transaction; ``GET /overlay``
answers its records and the undo and redo picks; a stale base, an open job and the cap are refused
and store nothing. On the fakes (the SwedeBeat gateway, spotdown's ``exact`` maps, the model) and
Postgres as ``classbeat_app``; never the network."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import user_edit
from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import blocks
from studiooutputs import outputs_on  # noqa: F401 - fixture
from studiotools import StudioEnv, StudioRider
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default", "outputs_on")


async def edit(rider: StudioRider, class_id: UUID, base: object, op: dict[str, Any]) -> Any:
    body = {"base_revision_id": str(base), "edit": op}
    return await rider.browser.post(f"/api/classes/{class_id}/edits", body)


def current_id(studio: StudioEnv, class_id: UUID) -> UUID:
    return studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()


def revision_count(studio: StudioEnv, class_id: UUID) -> int:
    return studio.songs.sql(
        "SELECT count(*) FROM class_revisions WHERE class_id = :c", c=class_id
    ).scalar_one()


def overlay_of(studio: StudioEnv, revision_id: UUID) -> Any:
    return studio.songs.sql(
        "SELECT edit_overlay FROM class_revisions WHERE id = :r", r=revision_id
    ).scalar_one()


def zone_of(studio: StudioEnv, revision_id: UUID, at: int) -> object:
    return blocks(studio.songs.revision(revision_id).content)[at].interval.get("zone")


def other_zone(studio: StudioEnv, revision_id: UUID, at: int) -> int:
    zone = zone_of(studio, revision_id, at)
    return 2 if zone != 2 else 3


async def test_a_set_stores_a_user_edit_revision_with_its_override(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    zone = other_zone(studio, base, 1)
    answer = await edit(
        rider, class_id, base, {"op": "set", "block": 1, "field": "zone", "value": zone}
    )
    assert answer.status_code == 200, answer.text
    made = answer.json()
    new = UUID(made["revision_id"])
    assert current_id(studio, class_id) == new
    stored = studio.songs.revision(new)
    before = studio.songs.revision(base)
    assert stored.provenance["origin"] == "user_edit"
    assert stored.provenance["base_revision_id"] == str(base)
    assert stored.provenance["edit"] == "set"
    assert stored.parent_id == base
    assert stored.music["mix_spec"] == before.music["mix_spec"]  # a set never changes the music
    assert zone_of(studio, new, 1) == zone
    overlay = overlay_of(studio, new)
    assert [(r["type"], r["field"], r["value"]) for r in overlay["records"]] == [
        ("override", "zone", zone)
    ]
    assert overlay["records"][0]["ai_value_at_set"] == zone_of(studio, base, 1)
    assert bytes(stored.content_hash) == revision_hash(
        stored.content, mix_spec=stored.music["mix_spec"], overlay=overlay
    )
    assert made["content_hash"] == bytes(stored.content_hash).hex()
    assert [r["type"] for r in made["records"]] == ["override"]
    assert "reference" not in json.dumps(made)  # no catalogue reference leaves


async def test_drag_keeps_the_total_and_records_the_fitted_boundary(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    was = studio.songs.revision(base).content
    answer = await edit(rider, class_id, base, {"op": "drag", "block": 0, "delta_sec": 20})
    assert answer.status_code == 200, answer.text
    new = UUID(answer.json()["revision_id"])
    now = studio.songs.revision(new).content
    total = lambda c: sum(b.end_ms - b.start_ms for b in blocks(c))  # noqa: E731
    assert total(now) == total(was)
    (record,) = overlay_of(studio, new)["records"]
    assert (record["field"], record["value"]) == ("boundary", blocks(now)[0].end_ms)
    assert record["ai_value_at_set"] == blocks(was)[0].end_ms


async def test_a_stale_base_is_refused_with_the_current_id_and_stores_nothing(
    studio: StudioEnv,
) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    first = await edit(rider, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    assert first.status_code == 200, first.text
    count = revision_count(studio, class_id)
    again = await edit(rider, class_id, base, {"op": "lock", "block": 1, "scope": "all"})
    assert again.status_code == 409
    assert again.json()["detail"] == {
        "reason": "stale_revision",
        "current_revision_id": first.json()["revision_id"],
    }
    assert revision_count(studio, class_id) == count


async def test_an_edit_while_a_job_is_open_is_class_busy(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    job = studio.songs.sql(
        "SELECT id FROM jobs WHERE class_id = :c ORDER BY created_at DESC LIMIT 1", c=class_id
    ).scalar_one()
    studio.songs.sql("UPDATE jobs SET state = 'queued', finished_at = NULL WHERE id = :j", j=job)
    count = revision_count(studio, class_id)
    answer = await edit(
        rider, class_id, proposal.current_revision_id, {"op": "lock", "block": 0, "scope": "all"}
    )
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "class_busy")
    assert revision_count(studio, class_id) == count


async def test_the_cap_counts_a_clear_and_not_an_undo(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(user_edit, "REVISION_LIMIT", 2)
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    one = await edit(rider, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    two = await edit(rider, class_id, one.json()["revision_id"], {"op": "clear_all"})
    assert (one.status_code, two.status_code) == (200, 200), two.text
    cleared = UUID(two.json()["revision_id"])
    assert overlay_of(studio, cleared) is None  # a fully cleared overlay is NULL
    three = await edit(rider, class_id, cleared, {"op": "lock", "block": 0, "scope": "all"})
    assert (three.status_code, three.json()["detail"]["reason"]) == (409, "revision_limit")
    view = (await rider.browser.get(f"/api/classes/{class_id}/overlay")).json()
    undo = view["undo"]
    picked = await rider.browser.post(
        f"/api/classes/{class_id}/current",
        {**undo, "expected_current_id": str(cleared)},
    )
    assert picked.status_code == 200, picked.text  # an undo past the cap still works
    assert str(current_id(studio, class_id)) == undo["revision_id"]


async def test_undo_and_redo_are_picks_and_a_branch_clears_the_redo(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    base = proposal.current_revision_id
    path = f"/api/classes/{class_id}/overlay"
    b = await edit(rider, class_id, base, {"op": "lock", "block": 0, "scope": "all"})
    b_id = b.json()["revision_id"]
    view = (await rider.browser.get(path)).json()
    assert view["revision_id"] == b_id
    assert view["undo"]["revision_id"] == str(base)
    assert view["redo"] is None
    pick = f"/api/classes/{class_id}/current"
    undone = await rider.browser.post(pick, {**view["undo"], "expected_current_id": b_id})
    assert undone.status_code == 200, undone.text
    view = (await rider.browser.get(path)).json()
    assert view["undo"] is None  # the base is the model's draft: nothing to undo
    assert view["redo"]["revision_id"] == b_id
    c = await edit(rider, class_id, base, {"op": "lock", "block": 1, "scope": "all"})
    c_id = c.json()["revision_id"]
    stale = await rider.browser.post(pick, {**view["redo"], "expected_current_id": str(base)})
    assert stale.status_code == 409
    assert stale.json()["detail"] == {"reason": "stale_revision", "current_revision_id": c_id}
    view = (await rider.browser.get(path)).json()
    assert view["undo"]["revision_id"] == str(base)
    again = await rider.browser.post(pick, {**view["undo"], "expected_current_id": c_id})
    assert again.status_code == 200, again.text
    view = (await rider.browser.get(path)).json()
    assert view["redo"]["revision_id"] == c_id  # the branch, not the old redo


async def test_a_spotify_class_answers_edit_unavailable(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    studio.songs.sql(
        "UPDATE classes SET music_source = jsonb_set(music_source, '{source}', '\"spotify\"') "
        "WHERE id = :c",
        c=class_id,
    )
    answer = await edit(
        rider, class_id, proposal.current_revision_id, {"op": "lock", "block": 0, "scope": "all"}
    )
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "edit_unavailable")
    view = await rider.browser.get(f"/api/classes/{class_id}/overlay")
    assert (view.status_code, view.json()["detail"]["reason"]) == (409, "edit_unavailable")
