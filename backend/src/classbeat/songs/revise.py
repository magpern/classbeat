"""The ``revise_songs`` job kind (ADR 0008 section 6, issue SJ3): "Change songs" in a revision.

- **Input** ``ReviseSongsInput`` (``classbeat.songs.inputs``), ``CONNECTIONS = ("mowl",
  "spotify")``; ``consumes_credit = False`` (a class is one credit however often it is revised).
  Server fields: ``base_revision_hash`` and ``request_message_id``.
- **admit** (the jobs API's create transaction, under the owner lock, the class row locked), each
  refusal writing nothing: ``song_picking_off``; ``invalid_input`` for a soundless class
  (``music_kind.refuse_soundless``, #530); ``invalid_input`` unless the base is a ``draft``
  of the class; ``stale_base`` unless it is the current revision; ``game_retired`` for a base with
  a game MOWL no longer offers (ADR 0029 section 3); ``revision_limit`` (it makes one
  chat revision, counted with ``revise_draft``'s); the month's model cost cap (409 ``quota``
  ``cost``, ``classbeat.quota.check_cost``); ``not_song_picked`` when the base draft has no
  ``music.song_list_id`` (a class on the user's own playlist keeps ``revise_draft``). Then:

  - mode ``pick``: ``chat_full``; ``not_connected``, ``consent_missing``, ``pool_busy``
    (``classbeat.songs.admit.pick_checks``); ``write_unresolved`` while a final-playlist write of
    the class is undecided (so phase 1 is not paid for first); ``invalid_input`` unless the base's
    list still loads and every ``replace`` slot is in it; the user's ``class_messages`` row is
    inserted with the job.
  - mode ``resume`` (``classbeat.songs.revise_resume``): ``not_connected``; the receipt rule of
    ``design_with_songs`` (``receipt_valid``) for a list a ``revise_songs`` job approved
    (``receipt_invalid``); that job's base must be this base (``stale_base``);
    ``account_changed``; ``write_unresolved`` while a final-playlist write of the class is
    undecided. No new message: the resume carries the approved job's ``request_message_id``.
- **Phase 1** (``mode = "pick"``, until ``AwaitApproval``): ``classbeat.songs.pick`` with the base
  list's songs outside ``replace`` as ``kept`` (``"all"`` keeps none) and the class's brief as of
  the base (``revise_brief``: its stated layer applied, #904), whose free text leads with the
  request (cut to the brief's 2000 characters); the new ``song_list`` (``provenance.origin =
  "revise_songs"``, parent: the base's list) marks the new songs ``new``. The approval is
  ``design_with_songs``' (``approve_check``).
  A class that states values first has the request read by the ``interpret`` stage and layered
  (``revise_reading``, ``revise_layer_policy``, as a design revise does, #1023): the pick is sized
  by the layered brief and the closed reading is kept in the list's ``provenance.reading`` (never
  the request's words), so phase 2 and a resume layer the same reading with no second run. The
  run is the job's own, under the song job's cost ceiling, and a catalogue replace skips it.
  A catalogue replace (ADR 0016 section 6, ``match_revision_id``) uses the server's slots and a
  status-only fact in place of ``replace`` and ``request``. The fact is the only Swedebeat
  material the model sees. After approval the job matches the new list
  (``classbeat.swedebeat_match.replace``) before phase 2 writes anything, and phase 2 clears
  ``classes.approved_swedebeat_match_id`` when the new draft becomes current.
- **Phase 2** (approved, or ``mode = "resume"``): ``classbeat.songs.phase2.final_music`` (prepare,
  which also requires the base to be still current before any Spotify write, a **new** final
  playlist with the full list, fill, attach, music); then the ``revise`` stage
  (ADR 0007 section 3, ``revise_phase2``) on the base draft, the new music, the requests along the
  parent chain and the request, told the songs changed; the final transaction:
  ``final_checks``, the draft revision (parent: the base, ``music.song_list_id``), the
  compare-and-set of ``current_revision_id`` **and** ``music_source`` (the new final playlist)
  from the base, the final playlist's MOWL id and the assistant message (a class whose
  ``music_source`` is ``none`` rolls it back ``invalid_input``, ``music_kind.check_stored``). If
  the base is no longer current the revision is kept, not current, and the class's music is
  unchanged. The earlier
  ``class_playlists`` row and its playlist are never touched (history).
"""

import uuid
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import quota
from classbeat.agent import Brief
from classbeat.context import UserContext
from classbeat.design import chat, limits, music_kind
from classbeat.design.games_policy import RETIRED
from classbeat.design.kind import current_runtime, group_model
from classbeat.flags import effective as flags
from classbeat.jobs import KINDS, JobFailure, JobKind, JobRun, Outcome
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations import orphans
from classbeat.quota.route import with_plan, with_plan_run
from classbeat.songs import admit as song_admit
from classbeat.songs import config, revise_phase2, revise_reading, song_list
from classbeat.songs.inputs import ReviseSongsInput
from classbeat.songs.phase2 import PHASE_TWO
from classbeat.songs.pick import SeedSpec, run_phase_one
from classbeat.songs.revise_brief import class_brief, pick_brief
from classbeat.songs.revise_resume import admit_resume
from classbeat.swedebeat_match import replace as catalogue_replace

KIND: Final = revise_phase2.ORIGIN
PHASE_ONE: Final = ("pool_search", "pool_write", "pool_analyse", "choose")
STAGES: Final = PHASE_ONE + tuple(s for s in PHASE_TWO if s != "design") + ("revise",)

_BASE_LIST = text(
    "SELECT music ->> 'song_list_id' AS song_list_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'draft'"
)


async def _base_list(db: AsyncSession, owner_id: UUID, class_id: UUID, base: UUID) -> UUID | None:
    """The base draft's ``music.song_list_id`` (None: not designed on picked songs)."""
    params = {"owner_id": owner_id, "class_id": class_id, "id": base}
    value = (await db.execute(_BASE_LIST, params)).scalar_one_or_none()
    try:
        return None if value is None else UUID(value)
    except ValueError:
        return None


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
    if not isinstance(parsed, ReviseSongsInput) or parsed.class_id is None:
        return Refused("invalid_input")
    # A catalogue replace checks the Swedebeat flag before the class or the match is read.
    connection_id: UUID | None = None
    if parsed.match_revision_id is not None:
        gated = await catalogue_replace.gate(db, ctx)
        if isinstance(gated, Refused):
            return gated
        connection_id = gated
    if not await flags.song_picking_admits(db, ctx, config.current().enabled):
        return Refused("song_picking_off")
    given, class_id, owner = parsed, parsed.class_id, ctx.user_id
    # ADR 0007 amendment item 9 (#530): a soundless class has no songs to change.
    soundless = await music_kind.refuse_soundless(db, owner, class_id)
    if soundless is not None:
        return soundless
    state = await chat.create_state(db, owner, class_id, given.base_revision_id)
    if not state.exists or state.base_kind != "draft" or state.base_hash is None:
        return Refused("invalid_input")  # a foreign or unknown id answers like any other
    if state.current != given.base_revision_id:
        return Refused("stale_base")
    if state.base_retired:
        return Refused(RETIRED)  # ADR 0029: a retired game is never revised on new songs
    chat_limits = limits.current()
    allowed = await chat_limits.policy.limit_for(db, ctx, class_id)
    if state.chat_revisions >= allowed:
        return Refused("revision_limit", {"limit": allowed, "count": state.chat_revisions})
    await quota.check_cost(db, ctx)  # QuotaRefused ``cost``: 409 ``quota`` (ADR 0011 section 4)
    list_id = await _base_list(db, owner, class_id, given.base_revision_id)
    if list_id is None:
        return Refused("not_song_picked")
    fields: dict[str, object] = {"base_revision_hash": state.base_hash.hex()}
    if given.mode == "resume":
        return await admit_resume(db, ctx, given, class_id, fields, KIND)
    if state.requests >= chat_limits.max_requests:
        return Refused("chat_full", {"limit": chat_limits.max_requests, "count": state.requests})
    refused = await song_admit.pick_checks(db, ctx, given.spotify_connection_id)
    if refused is not None:
        return refused
    if await orphans.playlist_unresolved(db, ctx, class_id):
        return Refused("write_unresolved")  # decided first, before phase 1 is paid for
    seed = await song_list.load(db, owner, class_id, list_id)
    if seed is None or (
        isinstance(given.replace, list)
        and any(slot >= len(seed.content.songs) for slot in given.replace)
    ):
        return Refused("invalid_input")
    catalogue: catalogue_replace.CatalogueReplace | None = None
    if given.match_revision_id is not None:
        if connection_id is None:
            return Refused("internal")
        decided = await catalogue_replace.decide(
            db,
            ctx,
            class_id=class_id,
            list_id=list_id,
            match_id=given.match_revision_id,
            connection_id=connection_id,
        )
        if isinstance(decided, Refused):
            return decided
        catalogue = decided
    message_id = uuid.uuid4()
    request = catalogue.fact if catalogue is not None else (given.request or "")

    async def after_insert(db: AsyncSession, job_id: UUID) -> None:
        try:
            await chat.insert_message(
                db,
                owner_id=owner,
                class_id=class_id,
                job_id=job_id,
                role="user",
                body=request,
                base_revision_id=given.base_revision_id,
                message_id=message_id,
            )
        except chat.MessageRefused:
            raise RuntimeError("the job is not a job of this class") from None

    stored = {**fields, "request_message_id": message_id}
    if catalogue is not None:
        stored["catalogue_replace"] = catalogue.slots
        stored["catalogue_fact"] = catalogue.fact
        stored["swedebeat_connection_id"] = catalogue.connection_id
    return Admitted(stored, after_insert)


async def _phase_one_plan(run: JobRun, given: ReviseSongsInput) -> tuple[Brief, str, SeedSpec]:
    owner, base = run.lease.owner_id, given.base_revision_id
    async with run.fenced() as fs:  # the brief as of the base: its stated layer applied (#904)
        found = await class_brief(fs.db, owner, run.lease.job_id, base)
        list_id = None
        if found is not None and given.class_id is not None:
            list_id = await _base_list(fs.db, owner, given.class_id, base)
    request = given.catalogue_fact if given.match_revision_id is not None else given.request
    if found is None or list_id is None or request is None:
        raise JobFailure("invalid_input")
    try:
        brief = Brief.model_validate(found)
    except ValidationError:
        raise JobFailure("internal") from None
    if given.match_revision_id is not None:
        if given.catalogue_replace is None:
            raise JobFailure("internal")
        slots: tuple[int, ...] | None = tuple(given.catalogue_replace)
    else:
        slots = None if given.replace == "all" or given.replace is None else tuple(given.replace)
    return brief, request, SeedSpec(list_id, slots)


async def run(job_run: JobRun) -> Outcome:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, ReviseSongsInput) or given.class_id is None:
        raise JobFailure("internal")
    if job_run.approved and given.mode != "pick":
        raise JobFailure("internal")  # a resume never waits
    songs = config.current()
    if job_run.approved or given.mode == "resume":
        if given.swedebeat_connection_id is not None:
            # The new list, before phase 2 writes a playlist (ADR 0016 section 6).
            named = (
                given.resume.song_list_revision_id
                if given.mode == "resume" and given.resume is not None
                else None
            )
            await catalogue_replace.rematch(job_run, given.swedebeat_connection_id, named)
        # Phase 2 revises the draft (the ``design`` group), before any Spotify or MOWL call.
        model = await group_model(runtime, job_run, given.route_key, "design")
        return await revise_phase2.run_revise_phase_two(
            job_run,
            given,
            keyring=runtime.keyring,
            provider=model.provider,
            agent=model.agent,
            config=songs,
            context_requests=runtime.context_requests,
        )
    brief, request, seed = await _phase_one_plan(job_run, given)
    # #1023: a request that states values is read first, as a revise reads it; the pick is sized
    # by the layered brief and the list keeps the reading for phase 2.
    stated, kept = await revise_reading.phase_one(job_run, runtime, given, songs, brief, request)
    model = await group_model(runtime, job_run, given.route_key, "songs")  # phase 1: songs
    return await run_phase_one(
        job_run,
        given,
        brief=pick_brief(stated.brief, request),
        seed=seed,
        extra_provenance=kept,
        origin=KIND,
        keyring=runtime.keyring,
        provider=model.provider,
        agent=model.agent,
        config=songs,
    )


REVISE_SONGS: Final = KINDS.register(
    JobKind(
        KIND,
        ReviseSongsInput,
        STAGES,
        with_plan_run(run),  # the current plan's gates for an input without any (#496)
        consumes_credit=False,
        admit=with_plan(admit),
        approve_check=song_admit.approve_check,
        server_fields=frozenset(
            {
                "base_revision_hash",
                "request_message_id",
                "route_key",
                "plan_gates",
                "catalogue_replace",
                "catalogue_fact",
                "swedebeat_connection_id",
            }
        ),
    )
)
