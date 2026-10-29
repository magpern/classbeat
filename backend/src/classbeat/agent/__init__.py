"""The agent loop (ADR 0006): a provider-neutral stage runner over ``ModelProvider``.

- ``provider``: the ``ModelProvider``/``Conversation`` interface and its value types;
- ``stage``, ``dispatcher``: stages with read and compute tools only, allowlists and caps;
- ``schemas``: the strict models of what the model hands back (``WorkoutDraft``, ``Brief``);
- ``model_input``: the one place model input is built, from allowlisted fields;
- ``design``: the ``design`` stage (``get_music``, ``preview_draft``, ``submit_draft``);
- ``loop``: ``run_stage`` with checkpoints and limits; ``limits``, ``prices``: the cost check;
- ``records``: fenced ``agent_runs`` writes and revision provenance;
- ``providers``: the adapters (``FakeProvider``; Anthropic in A2).

The agent never gets a write tool, a network client or credentials: the backend loads what a
stage needs before it runs and writes only around it, through ``classbeat.jobs``.
"""

from classbeat.agent.design import DesignContext, DesignResult, design_stage
from classbeat.agent.loop import AgentConfig, StageOutcome, run_stage
from classbeat.agent.model_input import MusicData
from classbeat.agent.records import JobStageHost, Provenance, provenance
from classbeat.agent.schemas import Brief, WorkoutDraft

__all__ = [
    "AgentConfig",
    "Brief",
    "DesignContext",
    "DesignResult",
    "JobStageHost",
    "MusicData",
    "Provenance",
    "StageOutcome",
    "WorkoutDraft",
    "design_stage",
    "provenance",
    "run_stage",
]
