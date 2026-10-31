"""The ``revise_songs`` job kind (ADR 0008 section 6, issue SJ3): "Change songs" in a revision.

- **Input** ``ReviseSongsInput`` (``classbeat.songs.inputs``), ``CONNECTIONS = ("mowl",
  "spotify")``; ``consumes_credit = False`` (a class is one credit however often it is revised).
  Server fields: ``base_revision_hash`` and ``request_message_id``.
- **admit** (the jobs API's create transaction, under the owner lock, the class row locked), each
  refusal writing nothing: ``song_picking_off``; ``invalid_input`` unless the base is a ``draft``
  of the class; ``stale_base`` unless it is the current revision; ``revision_limit`` (it makes one
  chat revision, counted with ``revise_draft``'s); the month's model cost cap (409 ``quota``
  ``cost``, ``classbeat.quota.check_cost``); ``not_song_picked`` when the base draft has no
  ``music.song_list_id`` (a class on the user's own playlist keeps ``revise_draft``). Then:

  - mode ``pick``: ``chat_full``; ``not_connected``, ``consent_missing``, ``pool_busy``
    (``classbeat.songs.admit.pick_checks``); ``write_unresolved`` while a final-playlist write of
    the class is undecided (so phase 1 is not paid for first); ``invalid_input`` unless the base's
    list still loads
    and every ``replace`` slot is in it; the user's ``class_messages`` row is inserted with the job.
  - mode ``resume``: ``not_connected``; the receipt rule of ``design_with_songs``
    (``receipt_valid``) for a list a ``revise_songs`` job approved (``receipt_invalid``); that job's
    base must be this base (``stale_base``); ``account_changed``; ``write_unresolved`` while a
    final-playlist write of the class is undecided. No new message: the resume carries the
    approved job's ``request_message_id``.
- **Phase 1** (``mode = "pick"``, until ``AwaitApproval``): ``classbeat.songs.pick`` with the base
  list's songs outside ``replace`` as ``kept`` (``"all"`` keeps none) and the class's stored brief,
  whose free text leads with the request (cut to the brief's 2000 characters); the new
  ``song_list`` (``provenance.origin = "revise_songs"``, parent: the base's list) marks the new
  songs ``new``. The approval is ``design_with_songs``' (``approve_check``).
- **Phase 2** (approved, or ``mode = "resume"``): ``classbeat.songs.phase2.final_music`` (prepare,
  which also requires the base to be still current before any Spotify write, a **new** final
  playlist with the full list, fill, attach, music); then the ``revise`` stage
  (ADR 0007 section 3) on the base draft, the new music, the requests along the parent chain and
  the request, told the songs changed; the final transaction: ``final_checks``, the draft
  revision (parent: the base, ``music.song_list_id``), the compare-and-set of
  ``current_revision_id`` **and** ``music_source`` (the new final playlist) from the base, the
  final playlist's MOWL id and the assistant message. If the base is no longer current the
  revision is kept, not current, and the class's music is unchanged. The earlier
  ``class_playlists`` row and its playlist are never touched (history).
"""

import hashlib
import json
import uuid
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections, quota
from classbeat.agent import AgentConfig, Brief, JobStageHost, WorkoutDraft, run_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.design import chat, diff, limits, revision
from classbeat.design.kind import current_runtime, group_model
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun, Outcome
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations import orphans
from classbeat.songs import admit as song_admit
from classbeat.songs import config, song_list
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import ReviseSongsInput
from classbeat.songs.phase2 import PHASE_TWO, final_checks, final_music, mark_mowl, music_source
from classbeat.songs.pick import SeedSpec, run_phase_one, stage_config

KIND: Final = "revise_songs"
PHASE_ONE: Final = ("pool_search", "pool_write", "pool_analyse", "choose")
STAGES: Final = PHASE_ONE + tuple(s for s in PHASE_TWO if s != "design") + ("revise",)
MAX_BRIEF_TEXT: Final = 2000

_BASE_LIST = text(
    "SELECT music ->> 'song_list_id' AS song_list_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'draft'"
)
_CLASS_BRIEF = text(
    "SELECT c.brief FROM jobs j JOIN classes c ON c.owner_id = j.owner_id AND c.id = j.class_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)
# The current pointer and the class's music move together, from the base only.
_CAS = text(
    "UPDATE classes c SET current_revision_id = :revision_id, "
    "music_source = CAST(:music_source AS jsonb), updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id = :base RETURNING c.id"
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
    if not config.current().enabled:
        return Refused("song_picking_off")
    given, class_id, owner = parsed, parsed.class_id, ctx.user_id
    state = await chat.create_state(db, owner, class_id, given.base_revision_id)
    if not state.exists or state.base_kind != "draft" or state.base_hash is None:
        return Refused("invalid_input")  # a foreign or unknown id answers like any other
    if state.current != given.base_revision_id:
        return Refused("stale_base")
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
        return await _admit_resume(db, ctx, given, class_id, fields)
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
    message_id = uuid.uuid4()
    request = given.request or ""

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

    return Admitted({**fields, "request_message_id": message_id}, after_insert)


async def _admit_resume(
    db: AsyncSession,
    ctx: UserContext,
    given: ReviseSongsInput,
    class_id: UUID,
    fields: dict[str, object],
) -> Admitted | Refused:
    if given.resume is None:  # the input model refuses it already
        return Refused("receipt_invalid")
    account = await connections.spotify_account_in(db, ctx, given.spotify_connection_id)
    if account is None:
        return Refused("not_connected")
    found = await song_admit.receipt_valid(
        db, ctx.user_id, class_id, given.resume.song_list_revision_id, KIND
    )
    if found is None:
        return Refused("receipt_invalid")
    try:
        approved = ReviseSongsInput.model_validate(found.approved_input)
    except ValidationError:
        return Refused("receipt_invalid")
    if approved.base_revision_id != given.base_revision_id:
        return Refused("stale_base")  # the list was made to change another version
    if approved.request_message_id is None:
        return Refused("receipt_invalid")
    if found.stored.account != account:
        return Refused("account_changed")
    if await orphans.playlist_unresolved(db, ctx, class_id):
        return Refused("write_unresolved")
    return Admitted({**fields, "request_message_id": approved.request_message_id})


def pick_brief(brief: Brief, request: str) -> Brief:
    """The brief phase 1 searches with: the request first, then the brief's own text, within the
    brief's 2000 characters (both are already clean text)."""
    joined = f"{request}\n\n{brief.text}" if brief.text else request
    return brief.model_copy(update={"text": joined[:MAX_BRIEF_TEXT].rstrip()})


async def _phase_one_plan(run: JobRun, given: ReviseSongsInput) -> tuple[Brief, SeedSpec]:
    async with run.fenced() as fs:
        found = (await fs.db.execute(_CLASS_BRIEF, fs.lease.params())).first()
        list_id = None
        if found is not None and given.class_id is not None:
            list_id = await _base_list(
                fs.db, fs.lease.owner_id, given.class_id, given.base_revision_id
            )
    if found is None or found.brief is None or list_id is None or given.request is None:
        raise JobFailure("invalid_input")
    try:
        brief = Brief.model_validate(found.brief)
    except ValidationError:
        raise JobFailure("internal") from None
    replace = None if given.replace == "all" or given.replace is None else tuple(given.replace)
    return pick_brief(brief, given.request), SeedSpec(list_id, replace)


async def run_revise_phase_two(
    run: JobRun,
    given: ReviseSongsInput,
    *,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
    context_requests: int,
) -> Complete:
    if given.base_revision_hash is None or given.request_message_id is None:
        raise JobFailure("internal")
    base_id, message_id = given.base_revision_id, given.request_message_id
    base_hash = bytes.fromhex(given.base_revision_hash)
    p, final, step = await final_music(run, keyring, given, config, base_revision_id=base_id)
    handed_over = False  # the outcome's ``release`` closes the step from then on
    try:
        async with run.fenced() as fs:
            base = await chat.load_base(
                fs.db, fs.lease.owner_id, fs.lease.job_id, base_id, context_requests
            )
            request = await chat.request_body(fs.db, fs.lease.owner_id, p.class_id, message_id)
        if base is None or base.kind != "draft" or not base.brief or request is None:
            raise JobFailure("invalid_input")
        if (
            base.content_hash != base_hash
            or hashlib.sha256(revision.canonical(base.content)).digest() != base_hash
        ):
            raise JobFailure("internal")  # a revision is immutable: the stored hash must hold
        try:
            brief = Brief.model_validate(base.brief)
            base_draft = WorkoutDraft.model_validate(base.content)
        except ValidationError:
            raise JobFailure("internal") from None
        ctx = ReviseContext.build(
            brief,
            step.data,
            base_draft,
            base.earlier_requests,
            request,
            music_changed=True,
            songs_changed=True,
        )
        stage = stage_config(
            agent, config, turns=agent.limits.max_turns, calls=agent.limits.max_tool_calls
        )
        outcome = await run_stage(
            JobStageHost(run), provider, revise_stage(ctx), ctx.first_message(), stage
        )
        if not isinstance(outcome.output, ReviseResult):
            raise JobFailure("internal")
        revised = outcome.output
        draft = revision.build(
            revised.result,
            outcome,
            ctx.map,
            step,
            origin=KIND,
            base_revision_id=base_id,
            base_hash=base_hash,
            request_message_id=message_id,
            song_list_id=p.revision_id,
        )
        change = diff.summarize(base.content, draft.content)
        mowl_playlist_id = step.data.playlist_id
        source = json.dumps(music_source(final), sort_keys=True, ensure_ascii=False)

        async def store(fs: FencedSession) -> UUID:
            """``final_checks``, the revision, the compare-and-set, the MOWL id, the message."""
            await final_checks(fs, p, final, step)
            revision_id = await draft.store(fs)
            params = {
                "revision_id": revision_id,
                "music_source": source,
                "job_id": fs.lease.job_id,
                "owner_id": fs.lease.owner_id,
                "base": base_id,
            }
            await fs.db.execute(_CAS, params)  # no row: kept, but not current
            await mark_mowl(fs, p, final, mowl_playlist_id)
            try:
                await chat.insert_message(
                    fs.db,
                    owner_id=fs.lease.owner_id,
                    class_id=p.class_id,
                    job_id=fs.lease.job_id,
                    role="assistant",
                    body=revised.note,
                    base_revision_id=base_id,
                    result_revision_id=revision_id,
                    change=change,
                )
            except chat.MessageRefused:
                raise JobFailure("internal") from None
            return revision_id

        complete = Complete(store, release=step.close)
        handed_over = True
        return complete
    finally:
        if not handed_over:
            step.close()


async def run(job_run: JobRun) -> Outcome:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, ReviseSongsInput) or given.class_id is None:
        raise JobFailure("internal")
    if job_run.approved and given.mode != "pick":
        raise JobFailure("internal")  # a resume never waits
    songs = config.current()
    if job_run.approved or given.mode == "resume":
        # Phase 2 revises the draft (the ``design`` group), before any Spotify or MOWL call.
        model = await group_model(runtime, job_run, given.route_key, "design")
        return await run_revise_phase_two(
            job_run,
            given,
            keyring=runtime.keyring,
            provider=model.provider,
            agent=model.agent,
            config=songs,
            context_requests=runtime.context_requests,
        )
    brief, seed = await _phase_one_plan(job_run, given)
    model = await group_model(runtime, job_run, given.route_key, "songs")  # phase 1: songs
    return await run_phase_one(
        job_run,
        given,
        brief=brief,
        seed=seed,
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
        run,
        consumes_credit=False,
        admit=admit,
        approve_check=song_admit.approve_check,
        server_fields=frozenset({"base_revision_hash", "request_message_id"}),
    )
)
