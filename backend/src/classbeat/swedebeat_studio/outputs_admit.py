"""The approval binding of ``produce_outputs`` (ADR 0036 section 6; ADR 0007 section 5; SR C6a).

The approval is the user's output choice for **one** studio draft: its id, its ``content_hash``
(over ``{content, mix_spec}``), the chosen outputs and the SwedeBeat connection with its
generation. ``admit`` (the jobs API's create transaction, under the owner lock) checks, in this
order, each refusal writing nothing:

1. ``consent_required`` without the consent route's session mark (``outputs_session``): only
   ``POST /api/classes/{id}/outputs``, after ``GET /auth/session``, creates this job;
2. ``feature_off`` unless the studio runs and the user's "I use Swedebeat" is on;
3. the draft (``current_draft.load`` with the class row share-locked): ``class_not_found``,
   ``no_studio_draft``, ``not_current`` (not the class's current revision), ``hash_mismatch``
   (a stale hash), ``internal`` (a stored hash that no longer holds);
4. the choice (``output_choice_policy.refusal``): ``invalid_input``, ``mowl_needs_mix``,
   ``output_off``;
5. the SwedeBeat connection: ``not_connected`` unless it is the user's, ``connected`` and live;
   ``connection_changed`` unless it is the one the approved list was matched with **and** its
   generation (read share-locked) is the one the route's session check saw;
6. for ``spotify_playlist``: ``not_connected`` unless the Spotify connection is the user's and
   ``connected``, ``account_changed`` unless it is the account the list was made for.

Accepted: the stored input gains ``swedebeat_generation`` and ``binding_version`` (server
fields). ``bound`` (checks 2
to 6) runs again from the database when the job starts (``outputs_kind``; ADR 0007 section 5,
point 2). Reasons are codes only; nothing of the rows is logged or returned.
"""

from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections
from classbeat.connections import Credentials, OpenConnection, hold
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.context import UserContext
from classbeat.flags import effective as flags
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.songs import config as song_config
from classbeat.studio.output_choice_policy import enabled_outputs, refusal
from classbeat.swedebeat_studio import config, current_draft
from classbeat.swedebeat_studio.admit import studio_on, swedebeat_connected
from classbeat.swedebeat_studio.current_draft import CurrentDraft
from classbeat.swedebeat_studio.outputs_input import BINDING_VERSION, ProduceOutputsInput
from classbeat.swedebeat_studio.outputs_session import consented

KIND: Final = "produce_outputs"
SERVER_FIELDS: Final = frozenset({"swedebeat_generation", "binding_version"})


async def enabled(db: AsyncSession, ctx: UserContext) -> frozenset[str]:
    """The outputs this environment runs for ``ctx``'s user (``output_choice_policy``)."""
    spotify_on = await flags.song_picking_admits(db, ctx, song_config.current().enabled)
    return enabled_outputs(config.current().values, spotify_on=spotify_on)


async def held(db: AsyncSession, ctx: UserContext, connection_id: UUID, generation: int) -> bool:
    """Share-lock the user's SwedeBeat row while it is still ``generation`` and ``connected``
    (``connections.hold``): no reconnect or disconnect can come between this and the commit."""
    opened = OpenConnection(connection_id, "swedebeat", Credentials(), b"", generation)
    return await hold(db, ctx, opened)


async def the_draft(
    db: AsyncSession, owner_id: UUID, given: ProduceOutputsInput, *, lock: bool
) -> CurrentDraft | str:
    """The approved draft, still the class's current one with the approved hash, or a reason."""
    found = await current_draft.load(db, owner_id, given.the_class, lock=lock)
    if isinstance(found, str):
        return "class_not_found" if found == "not_found" else found
    if found.revision_id != given.revision_id:
        return "not_current"
    if found.content_hash.hex() != given.content_hash:
        return "hash_mismatch"
    return found


async def _spotify(
    db: AsyncSession, ctx: UserContext, given: ProduceOutputsInput, found: CurrentDraft
) -> str | None:
    if "spotify_playlist" not in given.outputs:
        return None
    wanted = given.spotify_connection_id
    if wanted is None or ctx.connection("spotify") != wanted:
        return "not_connected"
    account = await connections.spotify_account_in(db, ctx, wanted)
    if account is None:
        return "not_connected"
    return "account_changed" if account != found.songs.music.spotify_user_id else None


async def bound(
    db: AsyncSession,
    ctx: UserContext,
    given: ProduceOutputsInput,
    generation: int | None,
    *,
    lock: bool,
) -> CurrentDraft | str:
    """Checks 2 to 6 of the module docstring: the draft, or a reason. ``generation`` is the one
    the session check saw (the create) or the approval stored (the run); ``None`` (the route's
    pre-check, before the session check) does not hold the row."""
    if not await studio_on(db) or not await uses_swedebeat_in(db, ctx.user_id):
        return "feature_off"
    found = await the_draft(db, ctx.user_id, given, lock=lock)
    if isinstance(found, str):
        return found
    refused = refusal(given.outputs, await enabled(db, ctx))
    if refused is not None:
        return refused
    connection_id = given.swedebeat_connection_id
    if not await swedebeat_connected(db, ctx, connection_id):
        return "not_connected"
    if str(connection_id) != found.songs.music.swedebeat_connection_id:
        return "connection_changed"
    if generation is not None and not await held(db, ctx, connection_id, generation):
        return "connection_changed"  # reconnected (or flipped) since that generation
    refused = await _spotify(db, ctx, given, found)
    return refused if refused is not None else found


async def admit(db: AsyncSession, ctx: UserContext, parsed: object, /) -> Admitted | Refused:
    if not isinstance(parsed, ProduceOutputsInput):
        return Refused("invalid_input")
    mark = consented()
    if mark is None:
        return Refused("consent_required")
    if mark.connection_id != parsed.swedebeat_connection_id:
        return Refused("connection_changed")
    done = await bound(db, ctx, parsed, mark.generation, lock=True)
    if isinstance(done, str):
        return Refused(done)
    return Admitted(
        fields={"swedebeat_generation": mark.generation, "binding_version": BINDING_VERSION}
    )
