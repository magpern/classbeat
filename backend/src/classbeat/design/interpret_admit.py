"""The ``interpret_request`` kind's input and its ``admit`` (ADR 0029 section 5, MA1b-3), split
out of ``classbeat.design.interpret_kind`` (which re-exports the input models).

**Admit** (``POST /api/jobs``, in the create transaction, under the owner lock, after the generic
checks: the MOWL connection is the user's current one, the per-user limit), reading only, in this
order: the plan's gates and the route key (``with_plan``: the request has no brief to check yet),
``not_connected`` for a Spotify connection id that is not the user's current one, then the period's
cost cap (``quota.check_cost``, 409 ``quota`` ``cost``). It then resolves, as server fields, what
the interpreter may be offered (and refuses ``needs_spotify``, or ``song_picking_unavailable`` when
song picking is off for the user, before any model call and with no credit, a request that has no
Spotify connection and can only end without music: roadmap Amendment 25,
``interpret_music_policy.without_spotify_is_refused``; never with the composer available, whose
sketch needs no Spotify, ADR 0038 amendment of 2026-11-24): ``sports`` (both, always: running
has no gate since ADR 0018 Amendment 6, #1544), ``pick_ready`` (a Spotify connection is
named and song picking admits the user), ``song_picking`` (song picking admits the user),
``swedebeat_mixes`` (the Swedebeat flag is on and the user uses Swedebeat) and ``composer``
(``design.composer``; ADR 0038 section 2). Without the composer a request with ``music_sources``
is refused ``feature_off``, and the reading is as before: only a job admitted with ``composer``
stores pinned songs, questions and a cycling class's music decision (``music_decision``). The
request is then stored as the class's first message (seq 1, role ``user``). The kind takes **no
class credit** (``consumes_credit=False``): the class credit is the design job's, taken by the
hand-off. It counts toward the per-user limit and its model cost toward the cost cap.
"""

from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import quota
from classbeat.agent.brief import Sport
from classbeat.context import UserContext
from classbeat.design import (
    chat,
    composer,
    interpret_policy,
    interpretation_row,
    limits,
    mixadmit,
    revision,
)
from classbeat.design.interpret_music_policy import no_music_reason, without_spotify_is_refused
from classbeat.design.kind import NoMusicChoice, SpotifyMusicChoice, SwedebeatMusicChoice
from classbeat.design.kind_music import song_picking
from classbeat.design.music_sources import MusicSources, check_sources
from classbeat.design.request_policy import ALL_SPORTS
from classbeat.design.revise import Request
from classbeat.jobs.inputs import ModelJobInput, Title, jsonb_size
from classbeat.jobs.kinds import Admitted, Refused


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
    song_picking: bool | None = None  # song picking admits the user (None: queued before it)
    swedebeat_mixes: bool | None = None
    composer: bool | None = None  # the composer flow was on at admit (None: before #1185)
    # A re-read of the class's own reading (#1536, ``classes.reread``): the interpret job whose
    # reading the instructor answered, and the answers as plain sentences, which are the next chat
    # message; ``request`` is the earlier request plus them. Set by the route, never a browser.
    reread_of: UUID | None = None
    answers: Request | None = None

    @model_validator(mode="after")
    def _one_class(self) -> Self:
        """A new class (overrides ``JobInput``'s rule of the same name), or, for a re-read, the
        reading's own class with its answers and no title."""
        if self.reread_of is not None:
            if self.class_id is None or self.title is not None or self.answers is None:
                raise ValueError("a re-read names its class and its answers, and no title")
            return self
        if self.class_id is not None or self.answers is not None:
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
    if parsed.reread_of is not None and (refused := await _reread_refusal(db, ctx, parsed)):
        return refused
    composing = await composer.composer_available(db, ctx)
    if parsed.music_sources is not None and not composing:
        return Refused("feature_off")
    await quota.check_cost(db, ctx)  # QuotaRefused ``cost``: 409 ``quota`` (ADR 0011 section 4)
    sports = list(ALL_SPORTS)  # running is always available (ADR 0018 Amendment 6, #1544)
    picking = await song_picking(db, ctx)
    pick_ready = spotify is not None and picking
    mixes = await mixadmit.flag_on(db, mixadmit.current().enabled) and await _uses_swedebeat(
        db, ctx
    )
    override = parsed.music_override
    if spotify is None and without_spotify_is_refused(
        music_source=None if override is None else override.source,
        sport_override=parsed.sport_override,
        mixes=mixes,
        composer=composing,
    ):  # Amendment 25: no model call and no credit; with song picking off, nothing to connect
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
            body=parsed.answers if parsed.reread_of is not None and parsed.answers else request,
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


_INTERPRET = "interpret_request"  # ``interpret_kind.KIND`` (which imports this module)
_DESIGNED = text(
    "SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id "
    "AND current_revision_id IS NOT NULL "
    "UNION ALL SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind <> :kind LIMIT 1"
)


_REQUESTS = text(
    "SELECT count(*) FROM class_messages WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND role = 'user'"
)


async def _reread_refusal(
    db: AsyncSession, ctx: UserContext, parsed: InterpretRequestInput
) -> Refused | None:
    """A re-read only of the reading the answers were derived from (``not_ready`` once another
    one was stored), and only before any design: a revision or a job of another kind (a design
    job, ``class_designed``), and while the chat holds fewer than its most requests (``chat_full``).
    An open job of the class is refused ``class_busy`` by the create."""
    class_id = parsed.class_id
    if class_id is None:
        return Refused("invalid_input")
    row = await interpretation_row.read(db, ctx.user_id, class_id)
    if row is None or row.job_id != parsed.reread_of:
        return Refused("not_ready")
    ids = {"owner_id": ctx.user_id, "class_id": class_id, "kind": _INTERPRET}
    if (await db.execute(_DESIGNED, ids)).first() is not None:
        return Refused("class_designed")
    # The answers are the chat's next message: a re-read counts toward the chat's request cap.
    asked = (await db.execute(_REQUESTS, ids)).scalar_one()
    max_requests = limits.current().max_requests
    if asked >= max_requests:
        return Refused("chat_full", {"limit": max_requests, "count": asked})
    return None


async def _uses_swedebeat(db: AsyncSession, ctx: UserContext) -> bool:
    from classbeat.connections.preferences import uses_swedebeat_in

    return await uses_swedebeat_in(db, ctx.user_id)
