"""The step of a ``revise_draft`` run that reads the revise request's stated values (ADR 0029
section 4, #904).

``layered(job_run, model, brief, request, gates)``, before the ``revise`` stage:

- a base brief that states nothing (``Brief.explicit`` empty) is returned as it is, with no
  model call, so a revise of a class without stated values runs exactly as before (nothing there
  is checked against the draft, so nothing can be refused twice);
- otherwise the ``interpret`` stage (``agent.interpret``: the same prompt, schema and eval key as
  a new request's) reads the request, told the class's values as ``current``
  (``interpret_input.current_values``: closed numbers, enums and games, never text), the class's
  own sport only, the plan's limits and no music names; then ``revise_layer_policy.layer`` layers
  what it read onto the base. The revise stage then gets the layered brief: its stated block is
  the layered one and its ``ExplicitGate`` checks the draft against it, so a miss after the
  correction budget is recorded as Not as asked.

The run is an ``agent_runs`` row of the job, shown under ``job_stage`` (``revise`` by default;
Change songs passes ``pool_search``), so it counts toward
the job's cost ceiling and the month's cost cap. The revision's provenance keeps the revise run's
usage only, as a design revision keeps its own and not its interpret job's. A failed interpret
fails the job like any stage: nothing is stored. ``read`` is the model call alone, for a job that
keeps the reading (Change songs, ``classbeat.songs.revise_reading``).
"""

from classbeat.agent import AgentConfig, run_stage
from classbeat.agent.brief import Brief
from classbeat.agent.interpret import InterpretContext, InterpretResult, interpret_stage
from classbeat.agent.interpret_input import current_values
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.provider import ModelProvider
from classbeat.agent.records import JobStageHost
from classbeat.design.revise_layer_policy import Layered, layer
from classbeat.jobs import JobFailure, JobRun
from classbeat.models.jobs import GroupModel
from classbeat.plan_gates import PlanGates

JOB_STAGE = "revise"


def context_for(brief: Brief, request: str, gates: PlanGates | None) -> InterpretContext:
    """What the interpreter is told of a revise: the request, the class's own sport, the plan's
    limits and the class's closed values as ``current`` (the suite's chained cases use it too)."""
    return InterpretContext(
        request,
        sports=(brief.sport,),
        max_class_minutes=None if gates is None else gates.max_class_minutes,
        games_allowed=True if gates is None else gates.games,
        current=current_values(brief),
    )


async def read(
    job_run: JobRun,
    provider: ModelProvider,
    agent: AgentConfig,
    brief: Brief,
    request: str,
    gates: PlanGates | None,
    *,
    job_stage: str = JOB_STAGE,
) -> InterpretedRequest | None:
    """The interpreter's reading of ``request``, or None for a brief that states nothing (no
    model call). ``agent`` carries the ceiling the run counts against; ``job_stage`` is the job's
    stage the run is shown under."""
    if not brief.explicit:
        return None
    ctx = context_for(brief, request, gates)
    outcome = await run_stage(
        JobStageHost(job_run, job_stage), provider, interpret_stage(ctx), ctx.first_message(), agent
    )
    if not isinstance(outcome.output, InterpretResult):
        raise JobFailure("internal")
    return outcome.output.request


async def layered(
    job_run: JobRun, model: GroupModel, brief: Brief, request: str, gates: PlanGates | None
) -> Layered:
    reading = await read(job_run, model.provider, model.agent, brief, request, gates)
    return Layered(brief) if reading is None else layer(brief, reading, gates)
