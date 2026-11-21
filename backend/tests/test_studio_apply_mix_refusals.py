"""#1369 (ADR 0036 amendment B, B4, B4a, B6 and B6b): every refusal of the mix choices, in the
preview and in the ``apply_mix_choices`` job, stores nothing; each case makes its gate fail.
On the fakes, against Postgres as ``classbeat_app``; never the network.

Each ``mix_plan_policy`` reason a choice can reach, and every reason on the same derivation, is
also shown at the calculation (``test_mix_apply_calc.py``). The order is pinned (a choice over
both the class length and the plan's maximum is ``class_length_out_of_tolerance`` in both), and
the proposal is found by its allowlist: a duplicate's revision 1 has none, an origin no one
listed is no proposal, and an applied or a beat-one draft is passed over.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

from classbeat.plan_gates import PlanGates
from classbeat.swedebeat_studio import apply_kind, mix_apply
from mixapplytools import (
    DJ,
    LENGTH,
    NO_MODEL,
    TRIMMED,
    WHOLE,
    applied,
    copy_revision,
    counts,
    create,
    current,
    designed,
    digest,
    preview,
    preview_of,
    run_apply,
    workout,
)
from studiotools import StudioEnv, StudioRider
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default")


def mix(*pieces: tuple[int, float, float]) -> dict[str, Any]:
    return {
        "tracks": [{"track": n, "start_sec": a, "end_sec": b} for n, a, b in pieces],
        "crossfades": [8] * (len(pieces) - 1),
    }


def whole(*tracks: int) -> list[tuple[int, float, float]]:
    return [(n, 0.0, LENGTH) for n in tracks]


# Every song cut to 120 s: composes to 904 s, far under the class's 1620 s.
SHORT = mix(*[(n, 0.0, 120.0) for n in range(8)])
# Track 0 plays twice; its second piece holds three of the workout's boundaries (570, 600, 630).
REPEAT = mix((0, 0.0, 100.0), *whole(1, 2), (0, 120.0, 240.0), *whole(3, 4, 5, 6, 7))


def gates(minutes: int = 60, games: bool = True) -> PlanGates:
    return PlanGates(max_class_minutes=minutes, games=games, races=True, ftp_tests=True)


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> Callable[[PlanGates], None]:
    """The user's plan gates when an apply is admitted."""

    def use(chosen: PlanGates) -> None:
        async def current_gates(*_: object) -> tuple[None, PlanGates]:
            return None, chosen

        monkeypatch.setattr(apply_kind, "current_gates", current_gates)

    return use


async def refused(
    studio: StudioEnv,
    rider: StudioRider,
    class_id: UUID,
    choices: Any,
    reason: str,
    *,
    in_preview: bool = True,
    base: UUID | None = None,
) -> dict[str, Any]:
    """The job (and, with ``in_preview``, the preview) refuses ``reason`` and stores nothing."""
    before = counts(studio, rider.user_id)
    at = current(studio, class_id)
    if in_preview:
        answer = await preview(rider, class_id, at, digest(studio, at), choices)
        assert answer.status_code in (200, 409), answer.text
        body = answer.json()
        got = body["refusal"]["reason"] if answer.status_code == 200 else body["detail"]["reason"]
        assert got == reason
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, choices, base)
    assert created.status_code == 409, created.text
    detail = created.json()["detail"]
    assert detail["reason"] == reason
    assert counts(studio, rider.user_id) == before
    assert current(studio, class_id) == at
    return detail


async def test_invalid_choices_and_a_stale_base_are_refused(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    await refused(studio, rider, class_id, {**DJ, "keep_whole": [8]}, "invalid_input")
    await refused(studio, rider, class_id, {**WHOLE, "blend": True}, "invalid_input")
    await refused(studio, rider, class_id, {**DJ, "colour": "red"}, "invalid_input")
    first = await applied(studio, rider, class_id, WHOLE)
    assert first.id != proposal
    await refused(studio, rider, class_id, DJ, "stale_base", in_preview=False, base=proposal)


async def test_no_proposal_for_a_duplicate_and_an_origin_nobody_listed(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    copy_revision(studio, proposal, "duplicate", None)  # revision 1 of a duplicated class
    await refused(studio, rider, class_id, WHOLE, "no_proposal")
    # An origin added later is no proposal until the allowlist names it (a denylist would take it).
    copy_revision(studio, proposal, "brand_new", None)
    at = current(studio, class_id)
    answer = await preview(rider, class_id, at, digest(studio, at), WHOLE)
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "no_proposal")


async def test_a_beat_one_or_an_applied_draft_is_never_the_proposal(studio: StudioEnv) -> None:
    rider, class_id, proposal = await designed(studio)
    copy_revision(studio, proposal, "beat_one", proposal)
    found = await preview_of(studio, rider, class_id, WHOLE)
    assert found["proposal_revision_id"] == str(proposal)
    first = await applied(studio, rider, class_id, WHOLE)
    assert first.provenance["proposal_revision_id"] == str(proposal)
    again = await preview_of(studio, rider, class_id, DJ)
    assert again["proposal_revision_id"] == str(proposal)
    # Applied on the applied draft instead, DJ would keep its hard cuts; from the proposal it has
    # the proposal's crossfades again.
    assert again["crossfades"] == [8] * 7


async def test_the_apply_limit(studio: StudioEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mix_apply, "MAX_CHOICE_APPLIES", 1)
    rider, class_id, _ = await designed(studio)
    await applied(studio, rider, class_id, WHOLE)
    detail = await refused(
        studio, rider, class_id, {**DJ, "keep_whole": [1]}, "apply_limit", in_preview=False
    )
    assert (detail["limit"], detail["count"]) == (1, 1)


async def test_the_class_length_comes_before_the_plan(
    studio: StudioEnv, plan: Callable[[PlanGates], None]
) -> None:
    plan(gates(minutes=10))  # 630 s at most: the moved workout would be far over it
    rider, class_id, _ = await designed(studio, SHORT)
    found = await preview_of(studio, rider, class_id, DJ)
    assert found["refusal"] == {"reason": "class_length_out_of_tolerance"}
    assert found["total_sec"] == pytest.approx(904, abs=5)
    detail = await refused(studio, rider, class_id, DJ, "class_length_out_of_tolerance")
    assert (detail["low_sec"], detail["high_sec"]) == (1620, 1980)


async def test_a_dropped_repeat_that_held_boundaries_does_not_fit(studio: StudioEnv) -> None:
    rider, class_id, _ = await designed(studio, REPEAT, workout(1836))
    choices = {**DJ, "keep_whole": [0]}
    found = await preview_of(studio, rider, class_id, choices)
    assert found["refusal"] is None  # the preview does not move the workout (B4)
    await refused(studio, rider, class_id, choices, "workout_does_not_fit", in_preview=False)


async def test_the_plan_gates(studio: StudioEnv, plan: Callable[[PlanGates], None]) -> None:
    plan(gates(minutes=30))  # 1890 s at most; whole songs make the class 1920 s
    rider, class_id, _ = await designed(studio)
    assert (await preview_of(studio, rider, class_id, WHOLE))["refusal"] is None
    await refused(studio, rider, class_id, WHOLE, "plan_class_too_long", in_preview=False)
    plan(gates(minutes=60, games=False))
    rider, class_id, _ = await designed(studio, TRIMMED, workout(game=True))
    await refused(studio, rider, class_id, WHOLE, "plan_not_allowed", in_preview=False)
    plan(gates(minutes=60, games=True))  # the same class under a plan with games applies
    assert (await applied(studio, rider, class_id, WHOLE)).provenance["origin"] == "mix_choices"


async def test_gone_analyses_are_music_changed_at_create_and_when_the_job_runs(
    studio: StudioEnv,
) -> None:
    rider, class_id, proposal = await designed(studio)
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, WHOLE)
    assert created.status_code == 201, created.text
    studio.songs.sql("DELETE FROM swedebeat_track_maps WHERE owner_id = :o", o=rider.user_id)
    before = counts(studio, rider.user_id)
    row = await run_apply(studio, UUID(created.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "music_changed")
    after = counts(studio, rider.user_id)
    assert after["class_revisions"] == before["class_revisions"]
    assert current(studio, class_id) == proposal
    await refused(studio, rider, class_id, DJ, "music_changed")
