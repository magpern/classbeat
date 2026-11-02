"""The ``design_draft`` job kind (ADR 0005 section 2, ADR 0006 section 3).

Input: ``{class_id | title, brief, music, mowl_connection_id}`` (``DesignDraftInput``), the music
being ``{source: "mowl_spotify_playlist", spotify_playlist_id}`` or, on the user's Swedebeat mix
(ADR 0016 section 3), ``{source: "mowl_swedebeat_mix", mix_id}``. Stages:

1. ``music`` (``classbeat.design.music``): the link or the attach, then the read-only analysis,
   with a checkpoint before every library read and the BPM wait inside the lease and budget; on
   a mix, first ``mixadmit.still_on`` (``feature_off`` when ``swedebeat_enabled`` is off now,
   before anything is called), then ``swedebeat_music.swedebeat_step`` (the cached analysis, or
   the daily slot, the listing, the attach and the analysis of the audio);
2. ``design``: ``classbeat.agent.run_stage`` with the ``design`` stage (``get_music``,
   ``preview_draft``, ``submit_draft``, built in A1) over the brief and the music summary, on a
   **new provider for every run** (a provider is never shared between jobs, #114 item 4). The
   ``design`` stage group's model comes from ``Runtime.models`` (``classbeat.models.jobs``):
   resolved once per attempt from the job's ``route_key``, before any MOWL call, so an unusable
   route fails ``model_config`` before anything is sent (ADR 0012 section 5);
3. store, in the final fenced transaction: the connection guard once more
   (``MusicStep.still_current``; ``not_connected`` rolls it back and fails the job), then the
   ``draft`` revision (``classbeat.design.revision``, provenance ``origin = design_draft``) and,
   for the class's first design, its ``brief``, ``music_source`` and ``current_revision_id``
   (ADR 0007 section 2); the job ends ``completed``.

It consumes the class's credit (``consumes_credit``, ADR 0007 section 3). Its ``admit`` sets the
``route_key`` of the user's effective plan (``classbeat.quota.route``, AD5 #223); on a mix it first
runs ``mixadmit.admit_mix`` (the flag, the MOWL connection, the user's own fresh listing, the daily
analysis limit). A Spotify playlist's job is admitted exactly as before.
The brief is refused at creation when it would not fit ``classes.brief`` (4096 bytes as jsonb),
so a long brief fails before the model runs, not in the final store.

The kind is registered in ``classbeat.jobs.KINDS`` when this package is imported: by the worker
(which runs it) and by the web app (the jobs API validates the input with the same registry,
J3). Only the worker ``configure``s the ``Runtime`` (the key ring and the model provider); a run
without one fails ``internal``. The kind never waits for approval.

Everything the pipeline reads about the user comes from the job's lease (``JobRun.ctx``, issued
from the claimed row) and the job's input; everything it writes goes through ``JobRun.fenced``.
"""

from dataclasses import dataclass
from typing import Annotated, ClassVar, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import AgentConfig, Brief, DesignContext, JobStageHost, design_stage, run_stage
from classbeat.agent.design import DesignResult
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.design import mixadmit, revision
from classbeat.design.guidance import rule_set_for
from classbeat.design.music import MusicStep, load_music
from classbeat.design.swedebeat_music import swedebeat_step
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import ModelJobInput, jsonb_size
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.models.jobs import GroupModel, ModelSource
from classbeat.models.store import StageGroup
from classbeat.quota.route import with_route_key

KIND: Final = "design_draft"
STAGES: Final = ("music", "design")

SpotifyPlaylistId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z]{22}$")]
# The opaque ``ID`` of MOWL's Swedebeat listing (the ``mowl_playlist_links`` check).
MixId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z_-]{1,64}$")]


class SpotifyMusicChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["mowl_spotify_playlist"]
    spotify_playlist_id: SpotifyPlaylistId


class SwedebeatMusicChoice(BaseModel):
    """The user's Swedebeat mix (ADR 0016 section 3): ``admit`` checks it is in their own
    listing (``classbeat.design.mixadmit``)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["mowl_swedebeat_mix"]
    mix_id: MixId


MusicChoice = Annotated[SpotifyMusicChoice | SwedebeatMusicChoice, Field(discriminator="source")]


class ClassSpotifyMusic(SpotifyMusicChoice):
    """``classes.music_source`` of a class on a Spotify playlist, and for a class designed on
    picked songs (ADR 0008 section 4) ``origin = "song_pick"`` and its ``class_playlists`` row.
    Never a job input: ``design_draft`` takes ``MusicChoice``, which forbids both fields."""

    origin: Literal["song_pick"] | None = None
    class_playlist_id: UUID | None = None


ClassMusicSource = Annotated[
    ClassSpotifyMusic | SwedebeatMusicChoice, Field(discriminator="source")
]
CLASS_MUSIC_SOURCE: Final = TypeAdapter[ClassSpotifyMusic | SwedebeatMusicChoice](ClassMusicSource)


class DesignDraftInput(ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    brief: Brief
    music: MusicChoice
    mowl_connection_id: UUID

    @model_validator(mode="after")
    def _brief_fits_the_class(self) -> Self:
        if jsonb_size(self.brief.model_dump(mode="json")) > revision.MAX_BRIEF_BYTES:
            raise ValueError("the brief is too long")
        return self


@dataclass(frozen=True)
class Runtime:
    """What the worker gives the kind: the key ring (to open the MOWL connection), the source of
    each stage group's model (a new provider per group and attempt, from the job's route), the
    agent's settings and the size of the conversation context of ``revise_draft``."""

    keyring: KeyRing
    models: ModelSource
    agent: AgentConfig
    context_requests: int = 10  # earlier requests ``revise_draft`` sends (CHAT_CONTEXT_REQUESTS)
    swedebeat_enabled: bool = False  # CLASSBEAT_SWEDEBEAT_ENABLED: a job on a mix checks it again


_runtime: Runtime | None = None


def configure(runtime: Runtime | None) -> None:
    """Set (or clear, with None) the worker's runtime."""
    global _runtime
    _runtime = runtime


def current_runtime() -> Runtime | None:
    """The worker's runtime, for the other kinds of this package."""
    return _runtime


def runtime_from_settings(settings: Settings, keyring: KeyRing) -> Runtime:
    # Imported here, in the worker only: the web app imports this module (to register the kind)
    # and must never load a provider adapter or the model SDK.
    from classbeat.models.providers import RouteModels

    return Runtime(
        keyring=keyring,
        models=RouteModels(settings),
        agent=AgentConfig.from_settings(settings),
        context_requests=settings.chat_context_requests,
        swedebeat_enabled=settings.swedebeat_enabled,
    )


async def group_model(
    runtime: Runtime, run: JobRun, route_key: str, group: StageGroup
) -> GroupModel:
    """The model of one stage group of this attempt (``classbeat.models.jobs``): its own new
    provider and the agent config of the route's choice."""
    return await runtime.models(run, route_key, group, runtime.agent)


async def music_step(
    run: JobRun,
    runtime: Runtime,
    music: SpotifyMusicChoice | SwedebeatMusicChoice,
    connection_id: UUID,
) -> MusicStep:
    """The music step of the class's source: the user's Spotify playlist in MOWL, or their
    Swedebeat mix (``swedebeat_step``; the caller checked the flag with ``mixadmit.still_on``)."""
    if isinstance(music, SwedebeatMusicChoice):
        return await swedebeat_step(run, runtime.keyring, music.mix_id, connection_id=connection_id)
    return await load_music(
        run, runtime.keyring, music.spotify_playlist_id, connection_id=connection_id
    )


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    """A Spotify playlist is admitted as before; a Swedebeat mix only through
    ``mixadmit.admit_mix`` (the flag, the MOWL connection, the user's own listing, the limit)."""
    if not isinstance(parsed, DesignDraftInput):
        return Refused("invalid_input")
    if isinstance(parsed.music, SwedebeatMusicChoice):
        refused = await mixadmit.admit_mix(db, ctx, parsed.music.mix_id, parsed.mowl_connection_id)
        if refused is not None:
            return refused
    return Admitted()


async def run(job_run: JobRun) -> Complete:
    runtime = _runtime
    if runtime is None:
        raise JobFailure("internal")
    given = job_run.input
    if not isinstance(given, DesignDraftInput) or job_run.approved:
        raise JobFailure("internal")
    if isinstance(given.music, SwedebeatMusicChoice):
        await mixadmit.still_on(job_run, runtime.swedebeat_enabled)  # before anything is called
    model = await group_model(runtime, job_run, given.route_key, "design")  # before any MOWL call
    await job_run.stage("music")
    step = await music_step(job_run, runtime, given.music, given.mowl_connection_id)
    handed_over = False  # the outcome's ``release`` closes the step from then on
    try:
        rules = rule_set_for(given.brief)  # nothing is dismissed on a new class
        ctx = DesignContext.build(given.brief, step.data, rules)
        stage = design_stage(ctx)
        outcome = await run_stage(
            JobStageHost(job_run), model.provider, stage, ctx.first_message(), model.agent
        )
        if not isinstance(outcome.output, DesignResult):
            raise JobFailure("internal")
        draft = revision.build(
            outcome.output,
            outcome,
            ctx.map,
            step,
            rules=rules.provenance() if rules else None,
        )

        async def store(fs: FencedSession) -> UUID:
            """In the final fenced transaction: the connection guard, then the revision. A
            ``not_connected`` here rolls it back and ends the job with that category."""
            await step.still_current(fs)
            revision_id = await draft.store(fs)
            await revision.adopt_first(
                fs,
                revision_id,
                given.brief.model_dump(mode="json"),
                given.music.model_dump(mode="json"),
            )
            return revision_id

        # The runner releases the step once the attempt is over, whether ``store`` ran or not
        # (a cancel, a lost lease or shutdown before it; #122 item 4).
        complete = Complete(store, release=step.close)
        handed_over = True
        return complete
    finally:
        if not handed_over:
            step.close()


DESIGN_DRAFT: Final = KINDS.register(
    JobKind(
        KIND,
        DesignDraftInput,
        STAGES,
        run,
        admit=with_route_key(admit),  # a mix's checks, then the plan's route key (AD5 #223)
        server_fields=frozenset({"route_key"}),
    )
)
