"""Every stage the model-path guards run over (design, revise, pick_pool, choose_songs, interpret),
built from the fakes: the real tool sets, so a guard sees what a provider is sent."""

import pytest

from agentfake import analysed, brief, good_draft
from classbeat.agent import DesignContext, design_stage
from classbeat.agent.choose_songs import choose_stage
from classbeat.agent.interpret import InterpretContext, interpret_stage
from classbeat.agent.pick_pool import pool_stage
from classbeat.agent.revise import ReviseContext, revise_stage
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import Stage
from pickfake import POOL_14, world


def all_stages(monkeypatch: pytest.MonkeyPatch) -> list[Stage]:
    music = analysed(monkeypatch)
    revise = ReviseContext.build(
        brief(),
        music,
        WorkoutDraft.model_validate(good_draft()),
        ("Longer warm-up",),
        "Harder",
        music_changed=False,
    )
    w = world()
    return [
        design_stage(DesignContext.build(brief(), music)),
        revise_stage(revise),
        pool_stage(w.pool),
        choose_stage(w.choose_context(POOL_14)),
        interpret_stage(InterpretContext("")),
    ]
