"""Read the request again with the instructor's answers (#1536, roadmap Amendment 37).

``POST /api/classes/{id}/reread`` (``classes.reread_routes``) turns the answers to the reading's
questions into one more ``interpret_request`` job on the **same** class, before any design:

- only for the user's own class (another user's, or an unknown one, is ``not_found``) whose reading
  is stored (``not_ready``); the checks that need the class and its jobs under the owner lock (no
  revision and no job of another kind, ``class_designed``; the reading is still the stored one,
  ``not_ready``; the chat is not full, ``chat_full``) are ``interpret_admit``'s, and an open job of
  the class is ``class_busy`` from the create;
- the body names the reading the answers belong to (``interpret_job_id``): another one stored since
  is ``not_ready``, before any index is read (a stale tab's answers are never applied to another
  reading's questions);
- "No music" makes the new job's ``music_override`` ``none``: an answer that picks the question's
  marked "No music" option (``no_music_index``: the stored question's own marker, never the
  option's words), or the body's closed ``no_music: true`` (#1538: a reading with no music question
  has no option to mark). A typed "no music" is not read (#1137). A soundless design the switch
  does not admit is refused at the hand-off, before any job or credit;
- ``music_sources`` (#1538: the confirm screen's playlist picker) are checked as the request's own
  are (``MusicSources``: Spotify ids, at most five, each once; the Spotify connection must be the
  user's current one, attached playlists need the composer, "No music" excludes them: the jobs
  API's ``invalid_input``, ``not_connected`` and ``feature_off``) and added to the ones the
  reading's job attached. Whether the user's own Spotify token can read each is the song search's
  check (``source_unreadable``), as for the request's own;
- the answers are checked against the stored questions and made into sentences
  (``design.interpret_reread_policy``: ``invalid_answer``); the new request is the request of the
  reading's own interpret job plus those sentences (``too_large`` over the request's limit), and
  the input is that job's browser fields (never its server fields) with the user's current
  connections, so a re-read after a reconnect works like ``interpret_again``;
- the job goes through the jobs API's create with the kind's own ``admit`` and the per-user limit,
  with **no class credit** (the kind has none; the design takes it, once), and its model cost
  counts toward the cost cap. Its idempotency key is derived from the reading's interpret job and
  the number of the class's interpret jobs that ended failed, cancelled or expired, so two
  concurrent calls (or a reopened tab) admit one job, and a retry after a failed or cancelled
  re-read is a new one;
- the answers are stored as the class's next chat message, in the create transaction; when the job
  completes its reading replaces the class's (``interpretation_row.store``), the hand-off's
  refusal, route and fill with it. The hand-off keys on the latest reading's job
  (``interpretation_row.handoff_key``), so the design uses the new reading.

Every read filters on ``owner_id`` and runs under RLS. Only codes and ids are answered.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.interpret_again import BROWSER_FIELDS
from classbeat.classes.interpretation import Handoff
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import (
    INTERPRET_REQUEST,
    interpret_reread_policy,
    interpretation_row,
)
from classbeat.design.interpret_admit import InterpretRequestInput, PickChoice
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy

Factory = async_sessionmaker[AsyncSession]

_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_READING_JOB = text(
    "SELECT input FROM jobs WHERE owner_id = :owner_id AND id = :job_id AND class_id = :class_id "
    "AND kind = :kind AND state = 'completed'"
)
_ENDED = text(
    "SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = :kind AND state IN ('failed', 'cancelled', 'expired')"
)


def reread_key(interpret_job_id: UUID, ended: int) -> str:
    """The new job's idempotency key: one per reading and per number of its re-reads that ended
    unfinished (failed, cancelled), so a repeat or a concurrent pair is one job and a failed
    re-read can be asked for again."""
    return f"reread-{interpret_job_id.hex}-{ended}"


async def reread(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    body: interpret_reread_policy.RereadIn,
    *,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Handoff:
    """The new interpret job (``created`` for the call that made it) or a reason code."""
    require_context(ctx)
    owner = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        row = await interpretation_row.read(db, ctx.user_id, class_id)
        if row is None:
            found = (await db.execute(_EXISTS, owner)).first()
            return Handoff(reason="not_ready" if found is not None else "not_found")
        if row.job_id != body.interpret_job_id:
            return Handoff(reason="not_ready")  # the answers belong to another reading
        params = {**owner, "job_id": row.job_id, "kind": INTERPRET_REQUEST.name}
        earlier = (await db.execute(_READING_JOB, params)).scalar_one_or_none()
        ended = (await db.execute(_ENDED, {**owner, "kind": INTERPRET_REQUEST.name})).scalar_one()
    if not isinstance(earlier, dict):
        return Handoff(reason="not_ready")
    mowl = ctx.connection("mowl")
    if mowl is None:
        return Handoff(reason="not_connected")
    said = interpret_reread_policy.sentences(row.questions, body)
    if said is None:
        return Handoff(reason="invalid_answer")
    no_music = interpret_reread_policy.wants_no_music(row.questions, body)
    try:
        given = InterpretRequestInput.model_validate(earlier)
        request = interpret_reread_policy.combined(given.request, said)
        if request is None:
            return Handoff(reason="too_large")
        keep: set[str] = {f for f in BROWSER_FIELDS if f != "title"}
        kept = given.model_dump(mode="json", include=keep, exclude_none=True)
        # Picked playlists are read with the user's Spotify, so they need its connection too.
        spotify = (
            ctx.connection("spotify")
            if given.spotify_connection_id or body.music_sources is not None
            else None
        )
        if spotify is None and body.music_sources is not None:
            return Handoff(reason="not_connected")
        if spotify is None and isinstance(given.music_override, PickChoice) and not no_music:
            return Handoff(reason="not_connected")  # a song pick needs Spotify
        parsed = InterpretRequestInput.model_validate(
            {
                **interpret_reread_policy.browser_fields(kept, body, no_music=no_music),
                "request": request,
                "class_id": str(class_id),
                "reread_of": str(row.job_id),
                "answers": said,
                "mowl_connection_id": str(mowl),
                **({"spotify_connection_id": str(spotify)} if spotify else {}),
            }
        )
    except ValidationError:
        return Handoff(reason="invalid_input")
    done = await enqueue(
        factory,
        ctx,
        parsed,
        INTERPRET_REQUEST,
        idempotency_key=reread_key(row.job_id, ended),
        ledger=ledger,
        limit=limit,
        consumes_credit=INTERPRET_REQUEST.consumes_credit,
    )
    if done.job_id is None:
        return Handoff(reason=done.reason or "invalid_input", counts=dict(done.counts))
    return Handoff(done.job_id, INTERPRET_REQUEST.name, done.state, done.created)
