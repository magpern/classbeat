"""The interpret step of Change songs (``revise_songs``; ADR 0029 section 4, issue #1023).

A song change whose request states values ("faster songs, 60 min") is read like a design revise:
the ``interpret`` stage (``classbeat.design.revise_stated``, the same prompt, schema and eval key)
reads the request, told the class's closed values, and ``revise_layer_policy.layer`` layers what
it read onto the class's brief. So the request's values are checked against the new reading, not
only against the earlier stated values, and a value beyond the plan's limit is Not as asked.

- **Phase 1** sizes the pick by the layered brief and keeps the reading in the new ``song_list``'s
  ``provenance.reading`` (``storable``: closed values only, no quote). The run is the job's, under
  its ``pool_search`` stage, and counts toward the song job's cost ceiling like every stage.
- **Phase 2** (the same job after Approve, or a resume job) reads it back from the approved list
  (``stored``) and layers it again, a pure step: no second model call, so a retried attempt or a
  resume is never billed twice. A list without a stored reading (made before this step existed)
  is read in phase 2 under the ``revise`` stage.
- A brief that states nothing, and a catalogue replace (its "request" is the server's own
  status-only fact, never the instructor's words), make no model call, as a design revise of such
  a class makes none.
"""

from typing import Any, Final

from pydantic import ValidationError

from classbeat.agent import AgentConfig
from classbeat.agent.brief import Brief
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.provider import ModelProvider
from classbeat.design import revise_stated
from classbeat.design.kind import Runtime, group_model
from classbeat.design.revise_layer_policy import Layered, layer
from classbeat.jobs import JobRun
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import ReviseSongsInput
from classbeat.songs.pick import stage_config

KEY: Final = "reading"
PICK_STAGE: Final = "pool_search"  # phase 1's first stage: the page's "Finding songs that fit"


def _without_quotes(value: Any) -> Any:
    """``value`` with every ``quote`` removed: the layering ignores them, so the request's words
    are not kept."""
    if isinstance(value, dict):
        return {k: _without_quotes(v) for k, v in value.items() if k != "quote"}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_without_quotes(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def storable(reading: InterpretedRequest) -> dict[str, Any]:
    return _without_quotes(reading.model_dump(mode="json", exclude_none=True))


def stored(provenance: dict[str, Any]) -> InterpretedRequest | None:
    """The reading a list's provenance keeps; None when absent or no longer valid."""
    raw = provenance.get(KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return InterpretedRequest.model_validate(raw)
    except ValidationError:
        return None


def _agent(agent: AgentConfig, songs: SongConfig) -> AgentConfig:
    """The interpreter's limits with the song job's cost ceiling, as the song stages have."""
    return stage_config(
        agent, songs, turns=agent.limits.max_turns, calls=agent.limits.max_tool_calls
    )


async def phase_one(
    run: JobRun,
    runtime: Runtime,
    given: ReviseSongsInput,
    songs: SongConfig,
    brief: Brief,
    request: str,
) -> tuple[Layered, dict[str, Any]]:
    """The class's brief with the request layered on it, and the provenance keys to store with
    the song list (none when nothing was read)."""
    if not brief.explicit or given.match_revision_id is not None:
        return Layered(brief), {}
    model = await group_model(runtime, run, given.route_key, "design")  # the interpret key's
    reading = await revise_stated.read(
        run,
        model.provider,
        _agent(model.agent, songs),
        brief,
        request,
        given.plan_gates,
        job_stage=PICK_STAGE,
    )
    if reading is None:
        return Layered(brief), {}
    return layer(brief, reading, given.plan_gates), {KEY: storable(reading)}


async def phase_two(
    run: JobRun,
    given: ReviseSongsInput,
    provider: ModelProvider,
    agent: AgentConfig,
    brief: Brief,
    request: str,
    list_provenance: dict[str, Any],
) -> Layered:
    """The same layering in phase 2: from the approved list's stored reading, else (an older
    list) one interpret run. ``agent`` is the revise stage's config (the song job's ceiling)."""
    if given.catalogue_fact is not None:
        return Layered(brief)
    reading = stored(list_provenance)
    if reading is None:
        reading = await revise_stated.read(run, provider, agent, brief, request, given.plan_gates)
    return Layered(brief) if reading is None else layer(brief, reading, given.plan_gates)
