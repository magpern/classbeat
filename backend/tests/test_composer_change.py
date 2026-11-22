"""A composer's change (ADR 0038 sections 6 and 8, #1188): a chat message submitted while the
fill's list waits becomes a ``change`` that replaces the waiting job; it always stores a new list
with the same songs and a new preview, the old job's approval is then refused ``superseded``, and
the sketch stays current. A change whose design no longer fits the songs (more than 10 %) is the
ordinary sketch revise: a new sketch, current, and no list. A refused change keeps the list
approvable and fails the message. Never the network, a real account or a model."""

import json
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.design import limits, music_kind
from composertools import SKETCH, ComposerEnv, change_script
from songtools import Rider, failing_script

pytestmark = pytest.mark.usefixtures("jenv")

WORDS = "Make the warm-up a bit longer."


async def send(rider: Rider, class_id: UUID, body: str = WORDS) -> httpx.Response:
    message = str(uuid.uuid4())
    base = f"/api/classes/{class_id}/pending-messages"
    stored = await rider.browser.post(base, {"message_id": message, "body": body})
    assert stored.status_code == 201, stored.text
    return await rider.browser.post(f"{base}/{message}/submit", None)


def jobs(cenv: ComposerEnv, class_id: UUID) -> list[Any]:
    return cenv.sql(
        "SELECT id, state, input ->> 'mode' AS mode FROM jobs WHERE class_id = :c "
        "AND kind = 'design_with_songs' ORDER BY created_at",
        c=class_id,
    ).all()


async def test_a_message_while_a_list_waits_is_a_change_with_a_new_list(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    submitted = await send(rider, class_id)
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["status"] == "submitted"
    change_id = UUID(submitted.json()["job_id"])
    assert [(r.state, r.mode) for r in jobs(cenv, class_id)] == [
        ("cancelled", "fill"),
        ("queued", "change"),
    ]
    late = await rider.approve(job_id, list_id, digest)
    assert late.json()["detail"]["reason"] == "superseded"
    cenv.songenv.script = change_script()
    row = await cenv.songenv.run(change_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    assert row.credit_state == "none"
    old, new = cenv.content(list_id), cenv.content(row.approval_revision_id)
    assert row.approval_revision_id != list_id
    assert [s["spotify_id"] for s in new["songs"]] == [s["spotify_id"] for s in old["songs"]]
    assert new["v"] == 3
    assert new["preview"]["design_run_id"] != old["preview"]["design_run_id"]
    assert cenv.current(class_id) == sketch
    assert cenv.drafts(class_id) == 1
    assert "pool_write" in cenv.songenv.stages(change_id)  # analysed again, in the songs' order


async def test_a_change_that_does_not_fit_the_songs_is_a_new_sketch(cenv: ComposerEnv) -> None:
    music_kind.configure(music_kind.SoundlessGate(enabled=True, fake_covers=True))
    try:
        rider = await cenv.songenv.rider()
        class_id, sketch = await cenv.sketched(rider)
        await cenv.waiting(rider, class_id)
        submitted = await send(rider, class_id, "Make it 40 minutes.")
        change_id = UUID(submitted.json()["job_id"])
        # The design (on the songs) and the sketch revise are both the ``revise`` stage, each on
        # a provider of its own: one scripted turn serves both, 40 minutes on 31 of songs.
        sketch_long = json.loads(json.dumps(SKETCH))
        sketch_long["segments"][0]["intervals"].insert(
            0, {"sec": 600, "zone": 2, "rpm": [85, 95], "standing": False}
        )
        for segment in sketch_long["segments"]:  # every track is spotdown's ``approximate``
            for interval in segment["intervals"]:  # timing (#1386): no cue the gate refuses
                interval.pop("cue", None)
        submit = {"draft": {"$draft": "sketch"}, "note": "Longer."}
        script = {
            "model": "fake-model",
            "drafts": {"sketch": sketch_long},
            "turns": [
                {
                    "only_in": "interpret",
                    "calls": [{"name": "submit_brief", "input": {}, "expect": {"accepted": True}}],
                },
                {"only_in": "revise", "calls": [{"name": "submit_draft", "input": submit}]},
            ],
        }
        cenv.songenv.script = script
        row = await cenv.songenv.run(change_id)
        assert (row.state, row.error_category) == ("completed", None)
        current = cenv.current(class_id)
        assert current not in (None, sketch)
        origin = cenv.sql(
            "SELECT provenance ->> 'origin' FROM class_revisions WHERE id = :r", r=current
        ).scalar_one()
        assert origin == "sketch"  # the fill hand-off starts again for it
        assert row.approval_revision_id is None
    finally:
        music_kind.configure(None)


async def test_a_refused_change_keeps_the_list_approvable(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    monkeypatch.setattr(
        limits, "_limits", limits.ChatLimits(policy=limits.SettingRevisionPolicy(0))
    )
    refused = await send(rider, class_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "revision_limit"
    assert cenv.songenv.row(job_id).state == "waiting_for_approval"
    message = (await rider.browser.get(f"/api/classes/{class_id}/pending-messages")).json()
    assert [(m["status"], m["reason"]) for m in message["messages"]] == [
        ("failed", "revision_limit")
    ]
    approved = await rider.approve(job_id, list_id, digest)
    assert approved.status_code == 200, approved.text


async def test_a_change_that_fails_while_running_leaves_the_sketch_and_find_songs_again(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    await cenv.waiting(rider, class_id)
    submitted = await send(rider, class_id)
    change_id = UUID(submitted.json()["job_id"])
    cenv.songenv.script = failing_script("auth")  # model_config: not retried
    row = await cenv.songenv.run(change_id)
    assert row.state == "failed", row.state
    assert cenv.current(class_id) == sketch
    assert cenv.drafts(class_id) == 1
    shown = (await rider.browser.get(f"/api/classes/{class_id}")).json()
    assert shown["fill_songs"]["failure"] == "cancelled"  # the replaced fill: Find songs again
    again = await cenv.fill(rider, class_id, retry=True)
    assert again.status_code == 201, again.text
