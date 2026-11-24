"""The ``interpret_request`` job kind (ADR 0029 section 5, MA1b-3): one free-text request in, the
brief, the music and the interpretation items out, stored on the class it creates. No design.

Input: ``{request, music_override?, music_sources?, sport_override?, mowl_connection_id,
spotify_connection_id?, title?}`` (``InterpretRequestInput``; ``music_sources`` is the
composer's attached music, ``design.music_sources``). The request is 1 to 2000 visible
characters, and is refused (422) when even the largest brief it could make
(``interpret_policy.worst_case_brief``) would not fit ``classes.brief``. It always creates a new
class: ``class_id`` is refused, and a missing title is taken from the request's first words
(``interpret_policy.title_for``; a model never sees a title).

**Admit** (``POST /api/jobs``, in the create transaction, under the owner lock, after the generic
checks: the MOWL connection is the user's current one, the per-user limit), reading only, in this
order: the plan's gates and the route key (``with_plan``: the request has no brief to check yet),
``not_connected`` for a Spotify connection id that is not the user's current one, then the period's
cost cap (``quota.check_cost``, 409 ``quota`` ``cost``). It then resolves, as server fields, what
the interpreter may be offered (and refuses ``needs_spotify``, or ``song_picking_unavailable`` when
song picking is off for the user, before any model call and with no credit, a request that has no
Spotify connection and can only end without music: roadmap Amendment 25,
``interpret_music_policy.without_spotify_is_refused``): ``sports`` (running only when the running
gate admits the user, ``sport.admit_running``), ``pick_ready`` (a Spotify connection is named and
song picking admits the user), ``song_picking`` (song picking admits the user), ``swedebeat_mixes``
(the Swedebeat flag is on and the user uses Swedebeat) and ``composer`` (the ``composer_flow`` flag,
``design.composer``; ADR 0038 section 2). With the flag off a request with ``music_sources`` is
refused ``feature_off``, and the reading is exactly as before: only a job admitted with ``composer``
stores pinned songs and questions (``interpret_composer_policy``). After the job row is inserted,
the request is stored as the class's first message (seq 1, role ``user``). The kind takes **no class
credit** (``consumes_credit=False``): the class credit is the design job's, taken by the hand-off.
It counts toward the per-user limit like any job, and its model cost toward the cost cap through its
``agent_runs``.

**Worker**: the ``design`` stage group's model first (an unusable route fails ``model_config``
before any call), then the ``interpret`` stage: the user's music names (``interpret_music``), the
``interpret`` agent stage (``agent.interpret``) and ``interpret_policy.resolve``. The final fenced
transaction stores ``classes.interpretation`` (``interpretation_row.store``), makes the reading's
short ``name`` the class's title while the title is still the request's first words
(``class_name.adopt``, #1233), and the job completes.
It **never enqueues** the design: the worker cannot reach the jobs API (``tests/session_scan.py``)
and the class's one-open-job index would refuse a design job while this one runs. The browser, or
the class page after a reopened tab, calls ``POST /api/classes/{id}/design-from-interpretation``
(``classbeat.classes.interpretation``). A failed interpret stores nothing and uses no credit.
"""

from typing import Annotated, ClassVar, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import quota
from classbeat.agent import run_stage
from classbeat.agent.brief import Sport
from classbeat.agent.interpret import InterpretContext, InterpretResult, interpret_stage
from classbeat.agent.interpret_input import MusicName
from classbeat.agent.records import JobStageHost
from classbeat.context import UserContext
from classbeat.design import (
    chat,
    class_name,
    composer,
    interpret_composer_policy,
    interpret_policy,
    interpretation_row,
    mixadmit,
    revision,
    sport,
)
from classbeat.design.interpret_music import music_options
from classbeat.design.interpret_music_policy import no_music_reason, without_spotify_is_refused
from classbeat.design.kind import (
    NoMusicChoice,
    SpotifyMusicChoice,
    SwedebeatMusicChoice,
    current_runtime,
    group_model,
)
from classbeat.design.kind_music import song_picking
from classbeat.design.music_sources import MusicSources, check_sources
from classbeat.design.revise import Request
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import ModelJobInput, Title, jsonb_size
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.quota.route import with_plan, with_plan_run

KIND: Final = "interpret_request"
STAGES: Final = ("interpret",)


class PickChoice(BaseModel):
    """A song pick (``design_with_songs``), chosen by the user."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["pick"]


MusicOverride = Annotated[
    SpotifyMusicChoice | SwedebeatMusicChoice | NoMusicChoice | PickChoice,
    Field(discriminator="source"),
]


class InterpretRequestInput(ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)

    request: Request
    music_override: MusicOverride | None = None
    music_sources: MusicSources | None = None
    sport_override: Sport | None = None
    mowl_connection_id: UUID
    spotify_connection_id: UUID | None = None
    title: Title | None = None
    # Server fields, set only by ``admit``.
    sports: list[Sport] | None = Field(default=None, max_length=2)
    pick_ready: bool | None = None
    song_picking: bool | None = (
        None  # song picking admits the user (None: a job queued before this field)
    )
    swedebeat_mixes: bool | None = None
    composer: bool | None = None  # the composer flow was on at admit (None: before #1185)

    @model_validator(mode="after")
    def _one_class(self) -> Self:
        """Always a new class (overrides ``JobInput``'s rule of the same name)."""
        if self.class_id is not None:
            raise ValueError("an interpretation starts a new class")
        if self.title is None:
            self.title = interpret_policy.title_for(self.request)
        return self

    @model_validator(mode="after")
    def _fits(self) -> Self:
        worst = interpret_policy.worst_case_brief(self.request)
        if jsonb_size(worst.stored()) > revision.MAX_BRIEF_BYTES:
            raise ValueError("the request is too long")
        if isinstance(self.music_override, PickChoice) and self.spotify_connection_id is None:
            raise ValueError("a song pick needs the Spotify connection")
        override = None if self.music_override is None else self.music_override.source
        check_sources(self.music_sources, override, self.spotify_connection_id)
        return self


_JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    if not isinstance(parsed, InterpretRequestInput):
        return Refused("invalid_input")
    spotify = parsed.spotify_connection_id
    if spotify is not None and ctx.connection("spotify") != spotify:
        return Refused("not_connected")
    composing = await composer.composer_available(db, ctx)
    if parsed.music_sources is not None and not composing:
        return Refused("feature_off")
    await quota.check_cost(db, ctx)  # QuotaRefused ``cost``: 409 ``quota`` (ADR 0011 section 4)
    running = await sport.admit_running(db, ctx) is None
    sports = ["cycling", "running"] if running else ["cycling"]
    picking = await song_picking(db, ctx)
    pick_ready = spotify is not None and picking
    mixes = await mixadmit.flag_on(db, mixadmit.current().enabled) and await _uses_swedebeat(
        db, ctx
    )
    override = parsed.music_override
    if spotify is None and without_spotify_is_refused(
        music_source=None if override is None else override.source,
        sport_override=parsed.sport_override,
        running_offered=running,
        mixes=mixes,
    ):
        # Amendment 25: no model call and no credit. With song picking off for the user there is
        # nothing to connect (the Spotify page is hidden): say so, without a link.
        return Refused(no_music_reason(picking))
    request = parsed.request

    async def after_insert(db: AsyncSession, job_id: UUID) -> None:
        class_id = (
            await db.execute(_JOB_CLASS, {"job_id": job_id, "owner_id": ctx.user_id})
        ).scalar_one()
        await chat.insert_message(
            db,
            owner_id=ctx.user_id,
            class_id=class_id,
            job_id=job_id,
            role="user",
            body=request,
            base_revision_id=None,
        )

    fields = {
        "sports": sports,
        "pick_ready": pick_ready,
        "song_picking": picking,
        "swedebeat_mixes": mixes,
        "composer": composing,
    }
    return Admitted(fields, after_insert)


async def _uses_swedebeat(db: AsyncSession, ctx: UserContext) -> bool:
    from classbeat.connections.preferences import uses_swedebeat_in

    return await uses_swedebeat_in(db, ctx.user_id)


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, InterpretRequestInput) or job_run.approved:
        raise JobFailure("internal")
    model = await group_model(runtime, job_run, given.route_key, "design")  # before any call
    await job_run.stage("interpret")
    mixes = bool(given.swedebeat_mixes) and runtime.swedebeat_enabled
    options = await music_options(job_run, runtime.keyring, given.mowl_connection_id, mixes=mixes)
    gates = given.plan_gates
    sports = tuple(given.sports or ("cycling",))
    ctx = InterpretContext(
        given.request,
        sports=sports,
        max_class_minutes=None if gates is None else gates.max_class_minutes,
        games_allowed=True if gates is None else gates.games,
        music=tuple(MusicName(o.kind, o.name) for o in options),
        attached=len(given.music_sources or ()) if given.composer else 0,
    )
    outcome = await run_stage(
        JobStageHost(job_run),
        model.provider,
        interpret_stage(ctx),
        ctx.first_message(),
        model.agent,
    )
    if not isinstance(outcome.output, InterpretResult):
        raise JobFailure("internal")
    offer = interpret_policy.Offer(
        sports,
        gates,
        options,
        bool(given.pick_ready),
        given.spotify_connection_id is not None,
        given.song_picking is not False,
    )
    override = None if given.music_override is None else given.music_override.model_dump()
    resolved = interpret_policy.resolve(
        outcome.output.request,
        given.request,
        offer,
        sport_override=given.sport_override,
        music_override=override,
    )
    pins, questions = (
        interpret_composer_policy.composer_reading(outcome.output.request, resolved.brief)
        if given.composer
        else ([], [])
    )
    from_request = interpret_policy.title_for(given.request)
    stored = interpretation_row.StoredInterpretation(
        job_id=job_run.lease.job_id,
        brief=resolved.brief.stored(),
        music=resolved.music,
        items=resolved.items,
        pinned_songs=pins,
        questions=questions,
    )

    name = outcome.output.request.name if given.title == from_request else None

    async def store(fs: FencedSession) -> None:
        await interpretation_row.store(fs, stored)
        await class_name.adopt(fs, name, from_request)  # #1233: never a title the user chose

    return Complete(store)


INTERPRET_REQUEST: Final = KINDS.register(
    JobKind(
        KIND,
        InterpretRequestInput,
        STAGES,
        with_plan_run(run),
        consumes_credit=False,
        admit=with_plan(admit),
        server_fields=frozenset(
            {
                "route_key",
                "plan_gates",
                "sports",
                "pick_ready",
                "song_picking",
                "swedebeat_mixes",
                "composer",
            }
        ),
    )
)
