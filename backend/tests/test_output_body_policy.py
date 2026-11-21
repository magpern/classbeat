"""ADR 0036 amendment B8 (#1368): the render plan's bass swap is off by default
(``output_body_policy.render_plan``). Without the ``bass_swap`` value every transition is
``equal_power`` (no ``curve``), today's render before #1330; with it, #1330's plan exactly
(``test_mix_curve_policy`` runs #1330's tests with the value on). Pure: no I/O."""

from classbeat.studio import mix_curve_policy as mc
from classbeat.studio import output_body_policy as body
from test_mix_curve_policy import DURATIONS, FACTS, SPEC


def test_a_transition_that_would_swap_has_no_curve_by_default() -> None:
    assert any(s is not None for s in mc.bass_swaps(SPEC, FACTS))  # #1330 would swap here
    for plan in (
        body.render_plan(SPEC, DURATIONS, FACTS),  # the default: off
        body.render_plan(SPEC, DURATIONS, FACTS, bass_swap=False),
    ):
        assert all("curve" not in p and "swap" not in p for p in plan)
        assert plan == body.render_plan(SPEC, DURATIONS)  # equal_power everywhere: today's


def test_with_the_value_on_the_plan_is_1330s() -> None:
    on = body.render_plan(SPEC, DURATIONS, FACTS, bass_swap=True)
    swaps = mc.bass_swaps(SPEC, FACTS)
    for n, swap in enumerate(swaps):
        want = {} if swap is None else {"curve": "bass_swap", "swap": swap}
        assert {k: on[n + 1][k] for k in ("curve", "swap") if k in on[n + 1]} == want
    off = body.render_plan(SPEC, DURATIONS, FACTS)
    assert [{k: v for k, v in p.items() if k not in ("curve", "swap")} for p in on] == off
