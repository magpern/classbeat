"""The ``design_draft`` job kind (ADR 0005 section 2, ADR 0006 section 3).

Input: ``{class_id | title, brief, music, mowl_connection_id}`` (``DesignDraftInput``), the music
being ``{source: "mowl_spotify_playlist", spotify_playlist_id}`` or, on the user's Swedebeat mix
(ADR 0016 section 3), ``{source: "mowl_swedebeat_mix", mix_id}``, or for a cycling class with no
music (ADR 0007 amendment of 2026-11-03, #527) ``{source: "none"}``. Stages:

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

**A soundless class** (``NoMusicChoice``, ``classbeat.design.music_kind``): ``admit`` checks the
music kind of the class (``admit_music``) and the soundless gate (``admit_soundless``: the switch,
the job's own plan's route, every plan's route) before any credit is reserved; the worker checks
the switch again and that the attempt's model covers soundless, before any call. There is **no**
``music`` stage and no MOWL or Spotify call: the ``design`` stage is ``classbeat.agent.soundless``
over the brief alone, the step is a ``NoMusicStep``, the revision's music ``{"source": "none"}``,
the class's first ``music_source`` the same, and ``music_kind.check_stored`` runs in the store.

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

from classbeat.agent import AgentConfig, Brief, DesignContext, JobStageHost, run_stage
from classbeat.agent.design import DesignResult
from classbeat.agent.running import design_stage_for
from classbeat.agent.soundless import SoundlessContext, soundless_design_stage
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.design import corrections, kind_music, mixadmit, music_kind, revision, sport
from classbeat.design.interpreted import with_interpretation
from classbeat.design.music import MusicStep, load_music
from classbeat.design.sport_rules import rules_for
from classbeat.design.swedebeat_music import swedebeat_step
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun, mail_handoff
from classbeat.jobs.inputs import ModelJobInput, jsonb_size
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.models.jobs import GroupModel, ModelSource
from classbeat.models.store import StageGroup
from classbeat.quota.route import with_plan, with_plan_run

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


class NoMusicChoice(BaseModel):
    """A cycling class with no music (ADR 0007 amendment of 2026-11-03, item 1; #527). Also the
    class's stored ``music_source`` (``{"source": "none"}``)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["none"]


MusicChoice = Annotated[
    SpotifyMusicChoice | SwedebeatMusicChoice | NoMusicChoice, Field(discriminator="source")
]


class ClassSpotifyMusic(SpotifyMusicChoice):
    """``classes.music_source`` of a class on a Spotify playlist, and for a class designed on
    picked songs (ADR 0008 section 4) ``origin = "song_pick"`` and its ``class_playlists`` row.
    Never a job input: ``design_draft`` takes ``MusicChoice``, which forbids both fields."""

    origin: Literal["song_pick"] | None = None
    class_playlist_id: UUID | None = None


ClassMusicSource = Annotated[
    ClassSpotifyMusic | SwedebeatMusicChoice | NoMusicChoice, Field(discriminator="source")
]
CLASS_MUSIC_SOURCE: Final = TypeAdapter[ClassSpotifyMusic | SwedebeatMusicChoice | NoMusicChoice](
    ClassMusicSource
)


class DesignDraftInput(ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    brief: Brief
    music: MusicChoice
    mowl_connection_id: UUID
    # Set by the Swedebeat hand-off (ADR 0016 section 7). A browser sending it is refused.
    handoff_id: UUID | None = None

    @model_validator(mode="after")
    def _brief_fits_the_class(self) -> Self:
        if jsonb_size(self.brief.stored()) > revision.MAX_BRIEF_BYTES:
            raise ValueError("the brief is too long")
        return self

    @model_validator(mode="after")
    def _running_on_a_mowl_playlist(self) -> Self:
        """ADR 0018 section 2: a running class designs on an existing MOWL playlist only (no
        Swedebeat mix in v1)."""
        if self.brief.sport == "running" and not isinstance(self.music, SpotifyMusicChoice):
            raise ValueError("a running class designs on a MOWL playlist only")
        return self

    @model_validator(mode="after")
    def _no_hand_off_without_music(self) -> Self:
        """A hand-off designs on a mix (ADR 0016 section 7), never on no music."""
        if self.handoff_id is not None and isinstance(self.music, NoMusicChoice):
            raise ValueError("a hand-off designs on a mix")
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
        step = await swedebeat_step(run, runtime.keyring, music.mix_id, connection_id=connection_id)
    else:
        step = await load_music(
            run, runtime.keyring, music.spotify_playlist_id, connection_id=connection_id
        )
    try:  # the class's beat-one corrections for this music (ADR 0007 amendment)
        return await corrections.attach(run, step)
    except BaseException:
        step.close()
        raise


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    """A Spotify playlist is admitted as before; a Swedebeat mix only through
    ``mixadmit.admit_mix`` (the flag, the MOWL connection, the user's own listing, the limit); no
    music only through ``music_kind.admit_soundless`` (the switch, the job's own plan's route,
    every plan's route). Every check here reads only: the jobs API reserves the credit after."""
    if not isinstance(parsed, DesignDraftInput):
        return Refused("invalid_input")
    # ADR 0018 (#509): the running gate and the class's fixed sport, before anything else is read.
    refused = await sport.admit_brief(db, ctx, parsed.brief, parsed.class_id)
    if refused is not None:
        return refused
    refused = await kind_music.admit_music_choice(
        db,
        ctx,
        soundless=isinstance(parsed.music, NoMusicChoice),
        mix_id=parsed.music.mix_id if isinstance(parsed.music, SwedebeatMusicChoice) else None,
        class_id=parsed.class_id,
        mowl_connection_id=parsed.mowl_connection_id,
    )
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
    if given.brief.sport == sport.RUNNING:
        await sport.still_on(job_run)  # ADR 0018 section 5: before anything is called
    if isinstance(given.music, SwedebeatMusicChoice):
        await mixadmit.still_on(job_run, runtime.swedebeat_enabled, new_design=True)
    if isinstance(given.music, NoMusicChoice):
        music_kind.still_on()  # #527: the switch, before anything is called
    model = await group_model(runtime, job_run, given.route_key, "design")  # before any MOWL call
    if given.brief.sport == sport.RUNNING:
        sport.require_evaluated(model)  # an evaluated model only (ADR 0012, issue #465)
    if isinstance(given.music, NoMusicChoice):
        music_kind.require_covered(model)  # a model evaluated for soundless only (#527)
        return await _run_soundless(job_run, given, model)
    await job_run.stage("music")
    step = await music_step(job_run, runtime, given.music, given.mowl_connection_id)
    handed_over = False  # the outcome's ``release`` closes the step from then on
    try:
        rules = rules_for(given.brief)  # nothing is dismissed on a new class
        ctx = DesignContext.build(given.brief, step.data, rules, given.plan_gates)  # #496
        stage = design_stage_for(ctx)  # the brief's sport's prompt and schema (ADR 0018)
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
        draft = with_interpretation(draft, given.brief, outcome.output.unmet, given.plan_gates)

        async def store(fs: FencedSession) -> UUID:
            """In the final fenced transaction: the connection guard, then the revision. A
            ``not_connected`` here rolls it back and ends the job with that category."""
            await step.still_current(fs)
            revision_id = await draft.store(fs)
            # Before ``adopt_first``, which writes ``music_source`` of a class with no current
            # revision whatever it holds (#589).
            await music_kind.check_stored(fs, soundless=False)  # the class's music kind (#527)
            adopted = await revision.adopt_first(
                fs,
                revision_id,
                given.brief.stored(),  # the sport written explicitly (ADR 0018 section 1)
                given.music.model_dump(mode="json"),
            )
            await sport.check_stored(fs, given.brief.sport)  # the class's sport never changes
            if not adopted and isinstance(given.music, SwedebeatMusicChoice):
                # A verified hand-off designs on the mix, replacing the Spotify source.
                from classbeat.swedebeat_handoff.adopt import adopt_verified_mix

                await adopt_verified_mix(
                    fs, revision_id, given.music.mix_id, handoff_id=given.handoff_id
                )
            await mail_handoff.enqueue_class_ready(fs)  # ME5: only after a fresh mix analysis
            return revision_id

        # The runner releases the step once the attempt is over, whether ``store`` ran or not
        # (a cancel, a lost lease or shutdown before it; #122 item 4).
        complete = Complete(store, release=step.close)
        handed_over = True
        return complete
    finally:
        if not handed_over:
            step.close()


async def _run_soundless(job_run: JobRun, given: DesignDraftInput, model: GroupModel) -> Complete:
    """A soundless design (#527): no ``music`` stage and no MOWL or Spotify call; the soundless
    ``design`` stage over the brief, then the store with a ``NoMusicStep``. No rules section
    (``classbeat.agent.soundless_input`` says why)."""
    if given.brief.sport != sport.CYCLING:
        raise JobFailure("internal")  # the input refuses a running brief with no music
    step = music_kind.NoMusicStep()
    ctx = SoundlessContext(given.brief, given.plan_gates)  # #496
    outcome = await run_stage(
        JobStageHost(job_run),
        model.provider,
        soundless_design_stage(ctx),
        ctx.first_message(),
        model.agent,
    )
    if not isinstance(outcome.output, DesignResult):
        raise JobFailure("internal")
    draft = revision.build(outcome.output, outcome, None, step)
    draft = with_interpretation(draft, given.brief, outcome.output.unmet, given.plan_gates)

    async def store(fs: FencedSession) -> UUID:
        """In the final fenced transaction: the revision, the music kind checked, the class's first
        ``music_source`` of ``none``, then the sport and the music kind checked again (any of
        them rolls it all back)."""
        await step.still_current(fs)
        revision_id = await draft.store(fs)
        # A music source is refused before ``adopt_first`` can overwrite it (#589); an unset one
        # is the first design, which ``adopt_first`` sets to ``none``.
        await music_kind.check_stored(fs, soundless=True, before_adopt=True)
        await revision.adopt_first(fs, revision_id, given.brief.stored(), dict(music_kind.NO_MUSIC))
        await sport.check_stored(fs, given.brief.sport)
        await music_kind.check_stored(fs, soundless=True)  # strict, for a class already adopted
        return revision_id

    return Complete(store, release=step.close)


DESIGN_DRAFT: Final = KINDS.register(
    JobKind(
        KIND,
        DesignDraftInput,
        STAGES,
        with_plan_run(run),  # the current plan's gates for an input without any (#496)
        admit=with_plan(admit),  # the plan's gates, a mix's checks, the route key (AD5, #496)
        server_fields=frozenset({"route_key", "plan_gates", "handoff_id"}),
    )
)
