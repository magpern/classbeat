"""The composer's shared pieces (ADR 0038 sections 5 and 6, #1188), split out of
``classbeat.songs.composer``: the sketch read through the job's own class, the waiting list a swap
or change starts from, and the store of the new list (``await_list``): the Spotify connection
still the one used, the songs' music step still current, **the sketch still the class's current
revision**, then the ``song_list`` revision. No draft revision is stored and no pointer moves.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.agent.brief import Brief
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.stated_layer import applied
from classbeat.connections import OpenConnection, hold
from classbeat.design.chat import stated_layer
from classbeat.design.revision_ready_policy import SKETCH
from classbeat.jobs import AwaitApproval, FencedSession, JobFailure, JobRun
from classbeat.songs import composer_design, song_list
from classbeat.songs.composer_pick import Picked
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick import generations
from classbeat.songs.seed import SeedSpec, class_of, load_seeded
from classbeat.songs.song_list import MAX_UNMET, SongSource
from classbeat.spotdown.song_analysis_record_model import timing_marks

_SKETCH = text(
    "SELECT r.content, r.content_hash, r.provenance ->> 'origin' AS origin, c.brief, "
    "c.dismissed_rules, c.current_revision_id = r.id AS current FROM classes c "
    "JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id AND r.id = :base "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_STILL_CURRENT = text(
    "SELECT 1 FROM classes c JOIN jobs j ON j.owner_id = c.owner_id AND j.class_id = c.id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.current_revision_id = :base FOR SHARE"
)


@dataclass(frozen=True, slots=True)
class Sketch:
    """What the composer reads of its sketch. ``brief`` is the class's stored brief with the
    nearest stated layer applied (``stated_layer.applied``), so the instructor's latest request
    (\"make it seven bursts\") is what the fill, the swap, the change and phase 2 design to, not
    the brief the first design stored (#1493)."""

    content: dict[str, Any]
    content_hash: bytes
    brief: Any
    dismissed_rules: Any


async def read_sketch(run: JobRun, base: UUID) -> Sketch:
    """The sketch, read through the job's own class: still a sketch and still current."""
    async with run.fenced() as fs:
        class_id = await class_of(fs)
        params = {"owner_id": fs.lease.owner_id, "class_id": class_id, "base": base}
        row = (await fs.db.execute(_SKETCH, params)).first()
        stated = await stated_layer(fs.db, fs.lease.owner_id, class_id, base)
    if row is None or row.origin != SKETCH or not row.current or not isinstance(row.content, dict):
        raise JobFailure("invalid_input")  # stale: the page starts again from the current sketch
    brief = row.brief
    if isinstance(brief, dict) and stated is not None:
        brief = applied(brief, stated)  # pyright: ignore[reportUnknownArgumentType]
    return Sketch(row.content, bytes(row.content_hash), brief, row.dismissed_rules)


async def still_current(fs: FencedSession, base: UUID) -> None:
    params = {"job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id, "base": base}
    if (await fs.db.execute(_STILL_CURRENT, params)).first() is None:
        raise JobFailure("invalid_input")  # another sketch is current: this list is of no class


def sources_of_list(content: song_list.SongListContent) -> dict[str, SongSource]:
    return {s.spotify_id: s.source for s in content.songs if s.source is not None}


async def waiting_list(
    run: JobRun, given: DesignWithSongsInput
) -> tuple[Any, song_list.SongListRevision]:
    seed = given.seed
    if seed is None:
        raise JobFailure("internal")
    spec = SeedSpec(seed.song_list_revision_id, tuple(seed.replace) or None)
    if given.mode == "change":
        spec = SeedSpec(seed.song_list_revision_id, None)  # only read: every song is kept
    seeded = await load_seeded(run, spec)
    async with run.fenced() as fs:
        class_id = await class_of(fs)
        found = await song_list.load(fs.db, fs.lease.owner_id, class_id, seed.song_list_revision_id)
    if seeded is None or found is None or found.content.preview is None:
        raise JobFailure("invalid_input")
    return seeded, found


def brief_of(sketch: Sketch) -> Brief:
    try:
        return Brief.model_validate(sketch.brief)
    except ValidationError:
        raise JobFailure("internal") from None


def dismissed_of(sketch: Sketch) -> tuple[str, ...]:
    value = sketch.dismissed_rules
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def _unmet_of(picked: Picked, made: composer_design.Designed) -> tuple[InterpretationItem, ...]:
    """The list's Not as asked items: this design's missed shape sections (a carried list's own
    are replaced by them) and the picks' (#1493); at most ``MAX_UNMET``."""
    kept = tuple(i for i in picked.unmet if not i.path.startswith("shape."))
    return (*made.unmet, *kept)[:MAX_UNMET]


def await_list(
    given: DesignWithSongsInput,
    spotify: OpenConnection,
    picked: Picked,
    made: composer_design.Designed,
    parent: UUID | None,
) -> AwaitApproval:
    step = made.step
    base = given.base_revision_id
    if base is None or spotify.account is None:
        step.close()
        raise JobFailure("internal")
    try:
        built = song_list.build(
            picked.chosen,
            account=spotify.account,
            provenance={**picked.provenance, "composer": given.mode},
            parent_id=parent,
            generations=generations(spotify, step),
            unmet=_unmet_of(picked, made),
            preview=made.preview,  # invalid_output over 64 KB, before anything is stored
            sources=picked.sources,
            approximate=frozenset(p for p, _, _ in timing_marks(picked.provenance)),
        )
    except BaseException:
        step.close()
        raise

    async def store(fs: FencedSession) -> tuple[UUID, bytes]:
        if not await hold(fs.db, fs.lease.ctx, spotify):
            raise JobFailure("not_connected")
        await step.still_current(fs)
        await still_current(fs, base)
        return await built.store(fs), built.content_hash

    return AwaitApproval(store, release=step.close)
