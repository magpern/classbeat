"""#1369 (ADR 0036 amendment B, B4 and B4a): the gates of ``apply_mix_choices`` beyond the shared
derivation, each shown able to fail with nothing stored: the base hash at create time, and in the
run the job's own class, the base hash it was admitted with and the plan gates stored with it;
``game_retired`` in the preview and the job alike; the timeline with an analysis it cannot read.
On the fakes, against Postgres as ``classbeat_app``; never the network."""

import copy
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import studio_timeline
from classbeat.plan_gates import PlanGates
from classbeat.swedebeat_studio import apply_kind, mix_apply
from mixapplytools import (
    NO_MODEL,
    WHOLE,
    copy_revision,
    counts,
    create,
    current,
    designed,
    digest,
    preview,
    preview_of,
    run_apply,
)
from songtools import new_key
from studiotools import StudioEnv, StudioRider
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")


def gates(minutes: int) -> PlanGates:
    return PlanGates(max_class_minutes=minutes, games=True, races=True, ftp_tests=True)


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> Callable[[PlanGates], None]:
    def use(chosen: PlanGates) -> None:
        async def current_gates(*_: object) -> tuple[None, PlanGates]:
            return None, chosen

        monkeypatch.setattr(apply_kind, "current_gates", current_gates)

    return use


async def admitted(studio: StudioEnv, rider: StudioRider, class_id: UUID) -> UUID:
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, WHOLE)
    assert created.status_code == 201, created.text
    return UUID(created.json()["id"])


def set_input(studio: StudioEnv, job_id: UUID, path: str, value: Any) -> None:
    """The stored input changed after the create (a stand-in for a row the run must not trust)."""
    studio.songs.sql(
        "UPDATE jobs SET input = jsonb_set(input, CAST(:path AS text[]), CAST(:value AS jsonb)) "
        "WHERE id = :id",
        path=path,
        value=json.dumps(value),
        id=job_id,
    )


async def fails(studio: StudioEnv, rider: StudioRider, job_id: UUID, category: str) -> None:
    before = counts(studio, rider.user_id)["class_revisions"]
    row = await run_apply(studio, job_id)
    assert (row.state, row.error_category) == ("failed", category)
    assert counts(studio, rider.user_id)["class_revisions"] == before


async def test_create_refuses_the_current_draft_under_another_hash(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    before = counts(studio, rider.user_id)
    body = {
        "kind": apply_kind.KIND,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(proposal),  # the current draft's id ...
            "base_content_hash": "ab" * 32,  # ... with a hash that is not its own
            "choices": WHOLE,
        },
    }
    answer = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "stale_base")
    assert counts(studio, rider.user_id) == before


async def test_the_run_rechecks_the_base_hash_it_was_admitted_with(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    job_id = await admitted(studio, rider, class_id)
    set_input(studio, job_id, "{base_content_hash}", "cd" * 32)
    await fails(studio, rider, job_id, "internal")
    assert current(studio, class_id) == proposal


async def test_the_run_reads_only_the_jobs_own_class(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _ = await designed(studio)
    job_id = await admitted(studio, rider, class_id)
    other = UUID(int=class_id.int ^ 1)  # an id the job's row does not name
    set_input(studio, job_id, "{class_id}", str(other))
    derived: list[object] = []
    real = mix_apply.derive

    async def spy(*args: Any, **kwargs: Any) -> Any:
        derived.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(mix_apply, "derive", spy)
    await fails(studio, rider, job_id, "internal")
    assert derived == []  # refused before anything was read or derived


async def test_the_run_holds_the_class_to_the_plan_gates_stored_with_it(
    studio: StudioEnv, plan: Callable[[PlanGates], None]
) -> None:
    plan(gates(60))
    rider, class_id, proposal = await designed(studio)
    job_id = await admitted(studio, rider, class_id)
    # The stored gates, not the user's plan now: 30 minutes (1890 s) refuses the 1920 s class.
    set_input(studio, job_id, "{plan_gates,max_class_minutes}", 30)
    await fails(studio, rider, job_id, "internal")
    assert current(studio, class_id) == proposal
    # An input with no gates is not one ``admit`` stored: refused before anything.
    again = await admitted(studio, rider, class_id)
    set_input(studio, again, "{plan_gates}", None)
    await fails(studio, rider, again, "internal")


async def test_a_retired_game_is_refused_in_the_preview_and_the_job(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    content = copy.deepcopy(studio.songs.revision(proposal).content)
    content["segments"][5]["intervals"][0].update({"game": "ghost_target", "zone": 4})
    copy_revision(studio, proposal, "design_draft", None, content)
    found = await preview_of(studio, rider, class_id, WHOLE)
    assert found["refusal"] == {"reason": "game_retired"}
    assert found["spec_digest"] is None
    before = counts(studio, rider.user_id)
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, WHOLE)
    assert (created.status_code, created.json()["detail"]["reason"]) == (409, "game_retired")
    assert counts(studio, rider.user_id) == before


async def test_the_timeline_answers_an_unreadable_analysis_as_music_changed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, proposal = await designed(studio)

    def unreadable(_: object) -> dict[str, Any]:
        raise ValueError("no positive duration")

    monkeypatch.setattr(studio_timeline, "_track_mix", unreadable)
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "music_changed")
    # And a preview of a draft whose stored hash does not hold is a 500, not a 409.
    studio.songs.sql(
        "UPDATE class_revisions SET content_hash = :h WHERE id = :r", h=b"\x00" * 32, r=proposal
    )
    bad = await preview(rider, class_id, proposal, digest(studio, proposal), WHOLE)
    assert (bad.status_code, bad.json()["detail"]["reason"]) == (500, "internal")
