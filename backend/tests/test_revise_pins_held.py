"""#1452: ``revise_pins.pinned_mix`` ends with B9's ``holds`` on the pinned spec. A pin the produced
mix does not hold is a correction round (``ToolError("invalid_mix")``), never a stored draft the
outputs would answer ``mix_not_applied``. Pure: fakes only, no I/O."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from classbeat.agent.stage import ToolError
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_pin_policy import Pinned
from classbeat.swedebeat_studio import revise_pins
from classbeat.swedebeat_studio.mix_apply_calc import plan_of
from mix_choice_fixtures import FACTS, ORDER, TARGET
from test_mix_revise_pin_policy import MODEL, record, stored


def test_a_pinned_mix_the_draft_does_not_hold_is_a_correction_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pin: PinRecord = record("b", "end", 218.7)
    good = stored((pin,))
    off = json.loads(json.dumps(good))
    off["tracks"][1]["end_sec"] += 2.0  # the pin put it at 218 s; the spec has it at 220 s
    music: Any = SimpleNamespace(references=ORDER, facts=lambda: FACTS, tracks=[])
    content = {"segments": [{"intervals": [{"sec": 60}]}]}
    monkeypatch.setattr(revise_pins, "revised", lambda *a: Pinned(off))
    monkeypatch.setattr(
        revise_pins, "compose_spec", lambda *a: SimpleNamespace(plan=plan_of(ORDER, off))
    )
    monkeypatch.setattr(revise_pins.mix_remap_calc, "remapped", lambda *a: [60])
    with pytest.raises(ToolError) as caught:
        revise_pins.pinned_mix(MODEL, SimpleNamespace(), content, music, TARGET, (pin,))  # type: ignore[arg-type]
    assert caught.value.code == "invalid_mix"
    assert any("pin_not_held" in m for m in caught.value.messages)
