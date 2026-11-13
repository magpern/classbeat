"""The seed of a replace (ADR 0008 section 6), split out of ``classbeat.songs.pick``: the list a
change of songs starts from, re-read and re-hashed in the worker through the job's own class."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from classbeat.agent.songs import KeptSong
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.songs import song_list
from classbeat.songs.inputs import Seed

_JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


@dataclass(frozen=True)
class SeedSpec:
    """The list a replace starts from and the slots to fill (``None``: every slot)."""

    song_list_revision_id: UUID
    replace: tuple[int, ...] | None

    @classmethod
    def of(cls, seed: Seed | None) -> "SeedSpec | None":
        return None if seed is None else cls(seed.song_list_revision_id, tuple(seed.replace))


@dataclass(frozen=True)
class Seeded:
    """A replace: the plan's kept songs and replaced roles, and the seed list (the parent)."""

    revision_id: UUID
    kept: tuple[KeptSong, ...]
    roles: tuple[str, ...]


async def class_of(fs: FencedSession) -> UUID:
    """The job's own class, from the fenced job row (never from the input)."""
    row = (await fs.db.execute(_JOB_CLASS, fs.lease.params())).first()
    if row is None:
        raise JobFailure("internal")
    return row.class_id


async def load_seeded(run: JobRun, seed: SeedSpec | None) -> Seeded | None:
    """The seed list re-read and re-hashed through the job's own class (``invalid_input`` when
    it no longer loads or a slot is not in it); ``admit`` checked the rest."""
    if seed is None:
        return None
    async with run.fenced() as fs:
        class_id = await class_of(fs)
        found = await song_list.load(
            fs.db, run.lease.owner_id, class_id, seed.song_list_revision_id
        )
    if found is None:
        raise JobFailure("invalid_input")
    slots = len(found.content.songs)
    replaced = set(range(slots)) if seed.replace is None else set(seed.replace)
    if not replaced or any(slot >= slots for slot in replaced):
        raise JobFailure("invalid_input")
    kept = tuple(
        KeptSong(slot, s.spotify_id, s.title, s.artist, s.role, s.bpm, s.length_sec, s.cover)
        for slot, s in enumerate(found.content.songs)
        if slot not in replaced
    )
    roles = tuple(found.content.songs[slot].role for slot in sorted(replaced))
    return Seeded(seed.song_list_revision_id, kept, roles)
