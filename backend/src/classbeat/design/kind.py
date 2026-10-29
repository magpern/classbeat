"""The ``design_draft`` job kind (ADR 0005 section 2, ADR 0006 section 3).

Input: ``{class_id | title, brief, music: {source: "mowl_spotify_playlist", spotify_playlist_id},
mowl_connection_id}`` (``DesignDraftInput``). Stages:

1. ``music`` (``classbeat.design.music``): the link or the attach, then the read-only analysis,
   with a checkpoint before every library read and the BPM wait inside the lease and budget;
2. ``design``: ``classbeat.agent.run_stage`` with the ``design`` stage (``get_music``,
   ``preview_draft``, ``submit_draft``, built in A1) over the brief and the music summary, on a
   **new provider for every run** (``Runtime.provider``; a provider is never shared between
   jobs, #114 item 4);
3. store: the connection guard once more, then the ``draft`` revision
   (``classbeat.design.revision``) in the final fenced transaction; the job ends ``completed``.

The kind is registered in ``classbeat.jobs.KINDS`` when this package is imported: by the worker
(which runs it) and by the web app (the jobs API validates the input with the same registry,
J3). Only the worker ``configure``s the ``Runtime`` (the key ring and the model provider); a run
without one fails ``internal``. The kind never waits for approval.

Everything the pipeline reads about the user comes from the job's lease (``JobRun.ctx``, issued
from the claimed row) and the job's input; everything it writes goes through ``JobRun.fenced``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, ClassVar, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from classbeat.agent import AgentConfig, Brief, DesignContext, JobStageHost, design_stage, run_stage
from classbeat.agent.design import DesignResult
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import provider_for
from classbeat.config import Settings
from classbeat.crypto.keks import KeyRing
from classbeat.design import revision
from classbeat.design.music import load_music
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput

KIND: Final = "design_draft"
STAGES: Final = ("music", "design")

SpotifyPlaylistId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z]{22}$")]


class MusicChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["mowl_spotify_playlist"]
    spotify_playlist_id: SpotifyPlaylistId


class DesignDraftInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    brief: Brief
    music: MusicChoice
    mowl_connection_id: UUID


@dataclass(frozen=True)
class Runtime:
    """What the worker gives the kind: the key ring (to open the MOWL connection), a factory
    that builds a new model provider for each run, and the agent's settings."""

    keyring: KeyRing
    provider: Callable[[], ModelProvider]
    agent: AgentConfig


_runtime: Runtime | None = None


def configure(runtime: Runtime | None) -> None:
    """Set (or clear, with None) the worker's runtime."""
    global _runtime
    _runtime = runtime


def runtime_from_settings(settings: Settings, keyring: KeyRing) -> Runtime:
    return Runtime(
        keyring=keyring,
        provider=lambda: provider_for(settings),
        agent=AgentConfig.from_settings(settings),
    )


def _new_provider(runtime: Runtime) -> ModelProvider:
    try:
        return runtime.provider()
    except Exception:  # ProviderUnavailable, a missing script: an operator problem
        raise JobFailure("model_config") from None


async def run(job_run: JobRun) -> Complete:
    runtime = _runtime
    if runtime is None:
        raise JobFailure("internal")
    given = job_run.input
    if not isinstance(given, DesignDraftInput) or job_run.approved:
        raise JobFailure("internal")
    provider = _new_provider(runtime)  # before any MOWL call; this run's own
    await job_run.stage("music")
    step = await load_music(
        job_run,
        runtime.keyring,
        given.music.spotify_playlist_id,
        connection_id=given.mowl_connection_id,
    )
    try:
        ctx = DesignContext.build(given.brief, step.data)
        stage = design_stage(ctx)
        outcome = await run_stage(
            JobStageHost(job_run), provider, stage, ctx.first_message(), runtime.agent
        )
        if not isinstance(outcome.output, DesignResult):
            raise JobFailure("internal")
        draft = revision.build(outcome.output, outcome, ctx.map, step)
        await step.still_current(job_run)
    finally:
        step.close()
    return Complete(draft.store)


DESIGN_DRAFT: Final = KINDS.register(JobKind(KIND, DesignDraftInput, STAGES, run))
