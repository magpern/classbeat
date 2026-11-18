"""A composer's swap replaces the waiting job (ADR 0038 section 6, #1188), against Postgres, the
fake Spotify, the fake MOWL and ``FakeProvider``: a swap refused at create keeps the waiting job
approvable; an accepted one cancels it in the same transaction, approving it is then refused
``superseded``, the swap waits on a new list with a new preview and the sketch stays current; a
swap that fails while running leaves the sketch current and Find songs again available; no credit
is used. Never the network, a real account or a model."""

from uuid import UUID

import pytest

from classbeat.design import limits
from composertools import ComposerEnv, swap, swap_script
from songtools import failing_script

pytestmark = pytest.mark.usefixtures("jenv")


async def test_a_swap_refused_at_create_keeps_the_waiting_job_approvable(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    monkeypatch.setattr(
        limits, "_limits", limits.ChatLimits(policy=limits.SettingRevisionPolicy(0))
    )
    refused = await swap(rider, class_id, list_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "revision_limit"
    assert cenv.songenv.row(job_id).state == "waiting_for_approval"
    assert cenv.sql("SELECT count(*) FROM jobs WHERE class_id = :c", c=class_id).scalar_one() == 2
    approved = await rider.approve(job_id, list_id, digest)
    assert approved.status_code == 200, approved.text  # still the user's list to approve
    assert cenv.current(class_id) == sketch


async def test_a_swap_replaces_the_waiting_job_and_waits_on_a_new_list(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    made = await swap(rider, class_id, list_id)
    assert made.status_code == 201, made.text
    swap_id = UUID(made.json()["job_id"])
    assert cenv.songenv.row(job_id).state == "cancelled"
    late = await rider.approve(job_id, list_id, digest)
    assert (late.status_code, late.json()["detail"]["reason"]) == (409, "superseded")
    cenv.songenv.script = swap_script()
    row = await cenv.songenv.run(swap_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    assert row.credit_state == "none"
    new_list = row.approval_revision_id
    assert new_list != list_id
    content = cenv.content(new_list)
    assert content["v"] == 3
    assert content["songs"][2]["new"] is True
    assert sum(1 for s in content["songs"] if s["new"]) == 1
    parent = cenv.sql("SELECT parent_id FROM class_revisions WHERE id = :r", r=new_list).scalar()
    assert parent == list_id
    assert cenv.current(class_id) == sketch
    assert cenv.drafts(class_id) == 1
    late = await rider.approve(job_id, list_id, digest)
    assert late.json()["detail"]["reason"] == "superseded"


async def test_a_swap_that_fails_while_running_leaves_the_sketch_and_find_songs_again(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id)
    made = await swap(rider, class_id, list_id)
    swap_id = UUID(made.json()["job_id"])
    cenv.songenv.script = failing_script("auth")  # model_config: not retried
    row = await cenv.songenv.run(swap_id)
    assert row.state == "failed", row.state
    assert cenv.current(class_id) == sketch
    assert cenv.drafts(class_id) == 1
    again = await cenv.fill(rider, class_id, retry=True)  # Find songs again
    assert again.status_code == 201, again.text


async def test_a_stored_swap_counts_toward_the_revise_limit_and_a_fill_does_not(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        limits, "_limits", limits.ChatLimits(policy=limits.SettingRevisionPolicy(1))
    )
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id)  # the fill: exempt, the limit is unused
    made = await swap(rider, class_id, list_id)
    assert made.status_code == 201, made.text
    cenv.songenv.script = swap_script()
    row = await cenv.songenv.run(UUID(made.json()["job_id"]))
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    again = await swap(rider, class_id, row.approval_revision_id)
    assert (again.status_code, again.json()["detail"]["reason"]) == (409, "revision_limit")
    assert again.json()["detail"]["count"] == 1
