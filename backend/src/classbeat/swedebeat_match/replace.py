"""Replacing songs Swedebeat does not have (ADR 0016 section 6, B3).

A catalogue replace is a ``revise_songs`` pick that names a ``swedebeat_match`` revision and
nothing else: the server sets ``replace`` to the missing and unsure positions and the request to
one fixed fact. The fact is statuses only (position and ``missing`` or ``unsure``). It never
carries a title, an artist, a duration, a catalogue id or a token, and neither does anything else
this module adds to a model's input. At most three such rounds per class.

After the user approves the new ``song_list``, the same job matches that list again
(``swedebeat_match``'s catalogue call, on the fakes in tests) and stores a new match revision.
A resume of that job matches only when that list has no match yet, so a retry does not search
twice. The flag is read before the match revision and again before any catalogue call; off, the
job is refused or ends ``feature_off`` and nothing is called. The user's own "I use Swedebeat"
preference (#525) is read with it at admission (``gate``) and before the rematch's catalogue call.
"""

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import find_in, open_connection
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.context import UserContext
from classbeat.flags import effective as flags
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.jobs.kinds import Refused
from classbeat.songs.song_list import load as load_song_list
from classbeat.swedebeat_match import config
from classbeat.swedebeat_match.revision import (
    MAX_SONGS,
    MatchContent,
    build,
)
from classbeat.swedebeat_match.revision import (
    load as load_match,
)

MAX_ROUNDS: Final = 3
PROVIDER: Final = "swedebeat"
# The fixed fact (ADR 0016 section 6). Statuses are appended; nothing else is.
FACT: Final = (
    "these songs are not in the Swedebeat catalogue; "
    "choose songs with the same role and a similar BPM"
)
_REPLACEABLE: Final = frozenset({"missing", "unsure"})

_ROUNDS = text(
    "SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'revise_songs' AND input ->> 'match_revision_id' IS NOT NULL"
)
_JOB_LIST = text(
    "SELECT class_id, approval_revision_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id"
)
_ALREADY = text(
    "SELECT 1 FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'swedebeat_match' AND parent_id = :parent_id"
)


def status_facts(content: MatchContent) -> list[dict[str, int | str]]:
    """The songs to replace, as the model may see them: position and status, nothing else."""
    return [
        {"position": song.position, "status": song.status}
        for song in content.songs
        if song.status in _REPLACEABLE
    ]


def replace_slots(content: MatchContent) -> list[int]:
    """Those songs as 0-based slots in the matched list (the match numbers from 1)."""
    return [song.position - 1 for song in content.songs if song.status in _REPLACEABLE]


def catalogue_fact(content: MatchContent) -> str:
    """The whole fact: the fixed sentence and the status list, one line, within a chat message."""
    text = f"{FACT} {json.dumps(status_facts(content), separators=(',', ':'))}"
    if not text or len(text) > 2000:
        raise ValueError("the catalogue fact does not fit a request")
    return text


@dataclass(frozen=True, slots=True)
class CatalogueReplace:
    """What ``admit`` stores for a catalogue pick: the slots, the fact, the connection."""

    slots: list[int]
    fact: str
    connection_id: UUID


async def gate(db: AsyncSession, ctx: UserContext) -> UUID | Refused:
    """The flag, the user's own "I use Swedebeat" preference (#525), then the user's connected
    Swedebeat row. No match revision is read here."""
    if not await flags.swedebeat_enabled(db, config.current().enabled):
        return Refused("feature_off")
    if not await uses_swedebeat_in(db, ctx.user_id):
        return Refused("feature_off")
    connection_id = ctx.connection(PROVIDER)
    if connection_id is None:
        return Refused("not_connected")
    info = await find_in(db, ctx, connection_id)
    if info is None or info.provider != PROVIDER or info.status != "connected":
        return Refused("not_connected")
    return connection_id


async def decide(
    db: AsyncSession,
    ctx: UserContext,
    *,
    class_id: UUID,
    list_id: UUID,
    match_id: UUID,
    connection_id: UUID,
) -> CatalogueReplace | Refused:
    """The replace this match allows, or a refusal. The flag again, before the revision is read."""
    if not await flags.swedebeat_enabled(db, config.current().enabled):
        return Refused("feature_off")
    found = await load_match(db, ctx.user_id, class_id, match_id)
    listed = await load_song_list(db, ctx.user_id, class_id, list_id)
    if found is None or listed is None:
        return Refused("invalid_input")
    bound = found.content.song_list_hash == listed.content_hash.hex()
    if found.song_list_id != list_id or not bound:
        return Refused("stale_match")
    slots = replace_slots(found.content)
    if not slots or any(slot >= len(listed.content.songs) for slot in slots):
        return Refused("invalid_input")
    params = {"owner_id": ctx.user_id, "class_id": class_id}
    count = int((await db.execute(_ROUNDS, params)).scalar_one())
    if count >= MAX_ROUNDS:
        return Refused("swedebeat_rounds", {"limit": MAX_ROUNDS, "count": count})
    return CatalogueReplace(slots, catalogue_fact(found.content), connection_id)


async def _list_of(fs: FencedSession, explicit: UUID | None) -> tuple[UUID, UUID] | None:
    row = (await fs.db.execute(_JOB_LIST, fs.lease.params())).one()
    list_id = explicit if explicit is not None else row.approval_revision_id
    if list_id is None:
        return None
    return row.class_id, list_id


async def rematch(run: JobRun, connection_id: UUID, song_list_id: UUID | None) -> None:
    """Match ``song_list_id`` (or the job's approved list) once. See the module docstring.

    ``feature_off`` and ``not_connected`` are raised before a catalogue client is opened. A list
    that already has a match is left as it is.
    """
    cfg = config.current()
    async with run.fenced() as fs:
        enabled = await flags.swedebeat_enabled(fs.db, cfg.enabled)
        if enabled:  # #525: the user's own switch too, before any catalogue call
            enabled = await uses_swedebeat_in(fs.db, run.lease.owner_id)
        named = await _list_of(fs, song_list_id)
    if not enabled:
        raise JobFailure("feature_off")
    if named is None or cfg.keyring is None:
        raise JobFailure("internal")
    class_id, list_id = named
    params = {**run.lease.params(), "class_id": class_id, "parent_id": list_id}
    async with run.fenced() as fs:
        already = (await fs.db.execute(_ALREADY, params)).first()
        stored = await load_song_list(fs.db, run.lease.owner_id, class_id, list_id)
    if already is not None:
        return
    if stored is None or len(stored.content.songs) > MAX_SONGS:
        raise JobFailure("invalid_input")
    if run.ctx.connection(PROVIDER) != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, cfg.keyring, run.ctx, PROVIDER)
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    # Imported here: this module is loaded while ``classbeat.songs`` is still initialising.
    from classbeat.swedebeat_match import kind as match_kind

    async with match_kind.catalogue_client() as client:
        songs = await match_kind.match(
            run, opened, client, list(stored.content.songs), cfg.pace_seconds
        )
    built = build(songs, song_list_id=list_id, song_list_hash=stored.content_hash)
    async with run.fenced() as fs:
        await built.store(fs)
