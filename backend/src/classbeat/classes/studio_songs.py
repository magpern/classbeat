"""A studio song list for the page and the user's actions on it (ADR 0036 section 3.3).

- ``view``: the user's own studio ``song_list`` of that class, its hash re-checked
  (``swedebeat_studio.song_list.load``), as the page shows it: per song its position, Spotify
  title, artist, role and cover, the SwedeBeat title, artist, duration and BPM, whether it is a
  gap, its swap record and which actions it allows; the totals, the evidence and whether the list
  can be approved. **No Spotify id and no catalogue reference** leave the server.
- ``act``: ``undo``, ``another`` or ``swap_anyway`` on a list, as **one** step in the jobs API's
  create transaction (``classbeat.jobs.hold``, under the owner lock): the job that made the list
  is cancelled when it still waits for the approval, then a ``studio_songs`` job of that action
  is created from that job's own brief and connections (and, for Swap anyway, its playlist),
  through ``studio_songs``' ``admit`` (the newest list, ended and never approved, the action
  fitting the list). A refusal rolls the
  cancel back, so a refused action leaves the waiting list as it was.

Every statement filters on ``owner_id`` as well as running under RLS; another user's class or
list is ``not_found`` like an unknown one. Try again later is the jobs API's own cancel.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import Held
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy, cancel
from classbeat.swedebeat_studio import song_list
from classbeat.swedebeat_studio.admit import KIND, studio_on
from classbeat.swedebeat_studio.inputs import Action, StudioSongsInput
from classbeat.swedebeat_studio.kind import STUDIO_SONGS
from classbeat.swedebeat_studio.song_list_model import StudioListContent
from classbeat.swedebeat_studio.swap_policy import askable, undoable

Factory = Any
ACTIONS: Final = ("undo", "another", "swap_anyway")

# The list's own job (the studio job that stored it), of the user's class.
_LIST_JOB = text(
    "SELECT j.id, j.state, j.input FROM class_revisions r JOIN jobs j ON j.owner_id = r.owner_id "
    "AND j.id = r.job_id WHERE r.owner_id = :owner_id AND r.class_id = :class_id "
    "AND r.id = :revision_id AND r.kind = 'song_list' AND j.kind = :kind"
)
_JOB_STATE = text("SELECT state FROM jobs WHERE owner_id = :owner_id AND id = :job_id FOR UPDATE")


def song_view(content: StudioListContent, position: int) -> dict[str, Any]:
    song = content.songs[position]
    facts = song.swedebeat
    swap = song.swap
    return {
        "position": position,
        "title": song.title,
        "artist": song.artist,
        "role": song.role,
        "cover": song.cover,
        "gap": song.gap,
        "swedebeat": None
        if facts is None
        else {
            "title": facts.title,
            "artist": facts.artist,
            "duration_sec": facts.duration_sec,
            "bpm": facts.bpm,
        },
        "swap": None
        if swap is None
        else {
            "original": {"title": swap.original.title, "artist": swap.original.artist},
            "replacement": {"title": swap.replacement.title, "artist": swap.replacement.artist},
            "reason": swap.reason,
            "score": swap.score,
            "undone": swap.undone,
        },
        "can_undo": undoable(content, position),
        "can_ask_another": askable(content, position),
    }


async def view(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> dict[str, Any] | str:
    """The page's view, or a reason: ``feature_off`` or ``not_found``."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return "feature_off"
        stored = await song_list.load(db, ctx.user_id, class_id, revision_id)
    if stored is None:
        return "not_found"
    content = stored.content
    return {
        "revision_id": revision_id,
        "content_hash": stored.content_hash.hex(),
        "target_sec": content.target_sec,
        "total_sec": content.total_sec,
        "evidence": content.evidence.model_dump(mode="json"),
        "rounds": content.rounds,
        "note": content.note,
        "approvable": content.evidence.state == "ok" and not content.has_gap(),
        "songs": [song_view(content, at) for at in range(len(content.songs))],
    }


class _Rollback(Exception):
    """A refused action: the cancel of the waiting job is rolled back with the transaction."""

    def __init__(self, reason: str, counts: Mapping[str, int | str]) -> None:
        super().__init__(reason)
        self.reason, self.counts = reason, dict(counts)


@dataclass
class _CancelWaiting:
    """The hold: cancel the list's waiting job before the create; roll back on a refusal."""

    ctx: UserContext
    job_id: UUID

    async def before(self, db: AsyncSession, /) -> Held | None:
        params = {"owner_id": self.ctx.user_id, "job_id": self.job_id}
        state = (await db.execute(_JOB_STATE, params)).scalar_one_or_none()
        if state == "waiting_for_approval":  # never an approved or running job
            await cancel(db, self.ctx, self.job_id)
        return None

    async def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Held:
        raise _Rollback(reason, counts)


@dataclass(frozen=True)
class Acted:
    job_id: UUID | None
    created: bool
    reason: str | None = None
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])
    state: str | None = None


async def act(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    action: Action,
    position: int | None,
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Acted:
    require_context(ctx)
    if action not in ACTIONS:
        return Acted(None, False, "invalid_input")
    params = {"owner_id": ctx.user_id, "class_id": class_id, "revision_id": revision_id}
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return Acted(None, False, "feature_off")
        row = (await db.execute(_LIST_JOB, {**params, "kind": KIND})).first()
    if row is None or not isinstance(row.input, dict):
        return Acted(None, False, "not_found")
    made: dict[str, Any] = row.input  # pyright: ignore[reportUnknownVariableType]
    try:
        parsed = StudioSongsInput.model_validate(
            {
                "class_id": str(class_id),
                "brief": made["brief"],
                "action": action,
                "seed": {"song_list_revision_id": str(revision_id), "position": position},
                "spotify_playlist_id": made.get("spotify_playlist_id")
                if action == "swap_anyway"
                else None,
                "spotify_connection_id": made["spotify_connection_id"],
                "swedebeat_connection_id": made["swedebeat_connection_id"],
            }
        )
    except (ValidationError, KeyError):
        return Acted(None, False, "invalid_input")
    try:
        done = await enqueue(
            factory,
            ctx,
            parsed,
            STUDIO_SONGS,
            idempotency_key=idempotency_key,
            ledger=ledger,
            limit=limit,
            consumes_credit=STUDIO_SONGS.consumes_credit,
            hold=_CancelWaiting(ctx, row.id),
        )
    except _Rollback as refused:
        return Acted(None, False, refused.reason, refused.counts)
    return Acted(done.job_id, done.created, done.reason, done.counts, done.state)
