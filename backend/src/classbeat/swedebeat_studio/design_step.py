"""The reads and checks the studio's design and revise jobs share (ADR 0036 sections 1 and 4, SR
C5). Every statement filters on ``owner_id`` as well as running under row security.

- ``approved_list``: a studio ``song_list`` of the class, made by a ``studio_songs`` job that was
  approved **for that list** and completed (the C3 pick's one approval), with whether it is the
  class's newest list, whether a draft was designed on it already, and the brief of that job (the
  class's brief until its first design). ``load_approved`` reads the list itself back (its hash
  re-checked, ``song_list.load``) and refuses a gap.
- ``pinned``: the bearer's SwedeBeat connection is still the one the list was made with.
- ``cached_maps``: the list's analyses from the user's cache only, for a revise: **no download**,
  and exactly the rows the base recorded (``studio_revision_model.wanted``: the analyser and the
  bytes, ADR 0040 section 6). A track whose analysis is gone (expired, forgotten by a
  Disconnect, or only another analyser's row is left) ends the job ``music_changed``: the music
  is not what the base was designed on, and Change songs designs again.
- ``adopt``: the class's first design adopts the brief and ``{"source": "swedebeat_studio"}``;
  a design on a newer approved list (a new pick) becomes the class's current revision.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.design import revision
from classbeat.design.studio_revision_model import StudioRevisionMusic, wanted
from classbeat.design.track_cache import TrackMap, cached, recorded
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.swedebeat_studio import song_list
from classbeat.swedebeat_studio.admit import KIND as SONGS_KIND
from classbeat.swedebeat_studio.song_list_model import SOURCE
from classbeat.swedebeat_studio.swap_policy import GapInMix, mix_tracks

STUDIO_SOURCE: Final = {"source": SOURCE}

_APPROVED = text(
    "SELECT j.input, (j.state = 'completed' AND j.approved_at IS NOT NULL "
    "AND j.approval_revision_id = r.id) AS approved, "
    "(SELECT n.id FROM class_revisions n WHERE n.owner_id = r.owner_id AND n.class_id = r.class_id "
    "AND n.kind = 'song_list' ORDER BY n.number DESC LIMIT 1) = r.id AS newest, "
    "EXISTS (SELECT 1 FROM class_revisions d WHERE d.owner_id = r.owner_id "
    "AND d.class_id = r.class_id AND d.kind = 'draft' "
    "AND d.music ->> 'song_list_id' = CAST(r.id AS text)) AS designed "
    "FROM class_revisions r JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.id = :list_id "
    "AND r.kind = 'song_list' AND j.kind = :kind"
)
_CURRENT = text(
    "UPDATE classes c SET current_revision_id = :revision_id, updated_at = now() FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id RETURNING c.id"
)


@dataclass(frozen=True, slots=True)
class Approved:
    approved: bool
    newest: bool
    designed: bool
    brief: Any


async def approved_list(
    db: AsyncSession, owner_id: UUID, class_id: UUID, list_id: UUID
) -> Approved | None:
    params = {"owner_id": owner_id, "class_id": class_id, "list_id": list_id, "kind": SONGS_KIND}
    row = (await db.execute(_APPROVED, params)).first()
    if row is None:
        return None
    made: Any = row.input if isinstance(row.input, dict) else {}
    return Approved(bool(row.approved), bool(row.newest), bool(row.designed), made.get("brief"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


async def load_approved(
    db: AsyncSession, owner_id: UUID, class_id: UUID, list_id: UUID
) -> song_list.StudioList | None:
    """The list, its hash re-checked, with no gap; None otherwise."""
    stored = await song_list.load(db, owner_id, class_id, list_id)
    if stored is None:
        return None
    try:
        mix_tracks(stored.content)
    except GapInMix:
        return None
    return stored


def pinned(bearer: SwedebeatBearer, stored: song_list.StudioList) -> None:
    """``not_connected`` unless the bearer's connection is the one the list was made with."""
    music = stored.music
    connection = bearer.connection
    if (
        str(connection.id) != music.swedebeat_connection_id
        or connection.generation != music.generations.swedebeat
    ):
        raise JobFailure("not_connected")


def references(stored: song_list.StudioList) -> list[str]:
    return [facts.reference for facts in mix_tracks(stored.content)]


async def cached_maps(
    run: JobRun, bearer: SwedebeatBearer, stored: song_list.StudioList, base: StudioRevisionMusic
) -> list[TrackMap]:
    """The list's analyses the base was designed on, from the cache, in order; ``music_changed``
    for one that is gone."""
    refs = references(stored)
    found = await cached(run, bearer.connection, refs, recorded(wanted(base, refs)))
    if any(r not in found for r in refs):
        raise JobFailure("music_changed")
    return [found[r] for r in refs]


async def adopt(fs: FencedSession, revision_id: UUID, brief: dict[str, Any]) -> None:
    """The first design adopts the brief and the studio source; a later one becomes current."""
    if await revision.adopt_first(fs, revision_id, brief, dict(STUDIO_SOURCE)):
        return
    params = {"revision_id": revision_id, "job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
    if (await fs.db.execute(_CURRENT, params)).first() is None:
        raise JobFailure("internal")
