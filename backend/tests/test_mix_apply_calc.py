"""#1369 (ADR 0036 amendment B, B4 and B6b) without a database: the proposal allowlist, the order of
the derivation's checks (``mix_apply.computed``, which the preview, the job's admit and its run
share) and the preview's rate limit."""

from typing import Any, cast
from uuid import uuid4

import pytest

from classbeat.classes.mix_preview_limit import PreviewLimiter
from classbeat.studio import mix_proposal_policy
from classbeat.studio.mix_choice_model import Proposal
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.swedebeat_studio import mix_apply
from mix_choice_fixtures import FACTS, ORDER, PLAIN, spec, track

DJ = {"dj_mix": True, "blend": True, "trim": True}
WHOLE = {"dj_mix": False, "blend": False, "trim": False}


class Music:
    """The part of ``StudioMusic`` the derivation reads before the fit: the stored facts."""

    def __init__(self, facts: dict[str, TrackFacts]) -> None:
        self._facts = facts

    def facts(self) -> dict[str, TrackFacts]:
        return self._facts


def found(value: Proposal, facts: dict[str, TrackFacts]) -> mix_apply.mix_proposal.Resolved:
    draft = mix_apply.mix_proposal.ProposalDraft(uuid4(), {}, cast(Any, None))
    return mix_apply.mix_proposal.Resolved(draft, cast(Any, Music(facts)), value)


def reason(value: Proposal, facts: dict[str, TrackFacts], choices: Any) -> str:
    with pytest.raises(mix_apply.MixApplyRefused) as caught:
        mix_apply.computed(found(value, facts), found(value, facts).draft, choices)
    return caught.value.reason


SHORT = track("exact", 50.0, 10.0, 40.0)  # a song under 60 s that plays as a loop run
LOOPED = spec(("a", 0.0, 240.0), ("e", 0.0, 40.0), ("e", 10.0, 50.0), crossfades=[8, 0])
LONG = track("exact", 3000.0, 600.0, 1200.0)  # whole, four of them are over 150 minutes
CUT_LONG = spec(*[(r, 0.0, 600.0) for r in ORDER], crossfades=[0, 0, 0])


@pytest.mark.parametrize(
    ("value", "facts", "choices", "expected"),
    [
        # the choices first: a position off the list, a form that is not whole songs, a stray key
        (Proposal(ORDER, PLAIN, 880), FACTS, {**DJ, "keep_whole": [4]}, "invalid_input"),
        (Proposal(ORDER, PLAIN, 880), FACTS, {**WHOLE, "trim": True}, "invalid_input"),
        (Proposal(ORDER, PLAIN, 880), FACTS, [], "invalid_input"),
        # ``checked``'s reasons a choice can reach (B4 step 3), named and never repaired
        (Proposal(("a", "e"), LOOPED, 300), {"a": FACTS["a"], "e": SHORT}, WHOLE,
         "window_too_short"),
        (Proposal(ORDER, CUT_LONG, 2400), dict.fromkeys(ORDER, LONG), WHOLE, "mix_too_long"),
        # the class length (B6), both ways
        (Proposal(ORDER, PLAIN, 600), FACTS, WHOLE, "class_length_out_of_tolerance"),
        (Proposal(ORDER, PLAIN, 1000), FACTS, DJ, "class_length_out_of_tolerance"),
    ],
)  # fmt: skip
def test_each_reason_of_the_derivation_in_its_order(
    value: Proposal, facts: dict[str, TrackFacts], choices: Any, expected: str
) -> None:
    assert reason(value, facts, choices) == expected


def test_a_mix_too_long_comes_before_the_class_length() -> None:
    # 12 000 s of whole songs: over 150 minutes and over the class's 2640 s; the first is named.
    value = Proposal(ORDER, CUT_LONG, 2400)
    assert reason(value, dict.fromkeys(ORDER, LONG), WHOLE) == "mix_too_long"


def test_the_class_length_refusal_carries_the_total_and_the_range() -> None:
    value = Proposal(ORDER, PLAIN, 600)
    with pytest.raises(mix_apply.MixApplyRefused) as caught:
        mix_apply.computed(found(value, FACTS), found(value, FACTS).draft, WHOLE)
    assert caught.value.total_sec == 960.0
    assert caught.value.counts == {"total_sec": 960, "low_sec": 540, "high_sec": 660}


def test_without_the_fit_a_good_choice_is_derived_from_the_proposal() -> None:
    value = Proposal(ORDER, PLAIN, 880)
    made = mix_apply.computed(found(value, FACTS), found(value, FACTS).draft, WHOLE)
    assert made.fitted is None
    assert [(t["start_sec"], t["end_sec"]) for t in made.spec["tracks"]] == [(0.0, 240.0)] * 4
    assert made.spec["crossfades"] == [0, 0, 0]


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("design_draft", "proposal"),
        ("revise_draft", "proposal"),
        ("mix_choices", "pass"),
        ("duplicate", "pass"),
        ("beat_one", "pass"),
        ("sketch", "pass"),
        ("design_with_songs", "pass"),
        ("brand_new", "pass"),  # an origin nobody listed is no proposal (an allowlist)
        (None, "pass"),
        (7, "pass"),
    ],
)
def test_the_proposal_is_an_allowlisted_origin(origin: object, expected: str) -> None:
    assert mix_proposal_policy.step(studio_draft=True, same_list=True, origin=origin) == expected


@pytest.mark.parametrize(("studio", "same"), [(False, True), (True, False), (False, False)])
def test_the_walk_stops_off_the_list_or_off_the_studio(studio: bool, same: bool) -> None:
    for origin in ("design_draft", "mix_choices"):
        assert (
            mix_proposal_policy.step(studio_draft=studio, same_list=same, origin=origin) == "stop"
        )


def test_the_preview_limit_is_per_user_and_per_window() -> None:
    now = [0.0]
    limiter = PreviewLimiter(per_user=2, clock=lambda: now[0])
    one, two = uuid4(), uuid4()
    assert [limiter.allow(one) for _ in range(3)] == [True, True, False]
    assert limiter.allow(two)  # another user has its own count
    now[0] = 60.0  # the next window
    assert limiter.allow(one)
