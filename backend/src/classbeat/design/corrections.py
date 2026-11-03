"""The class's beat-one corrections for a job's music (ADR 0007 amendment "beat-one corrections",
item 4, BC2).

A job that produces a draft reads, in one fenced transaction, the corrections of **its own class**
(``jobs.class_id`` of the fenced job row, the owner from the lease; never the job's input) for
**the fingerprint the music step just computed**, and carries them in ``MusicData.beat_one``.
``mix_input.music_map`` then applies them to its own copy of the map (``classbeat.agent.beat_one``),
so the fit, the warnings and the model's summary all see the corrected beat 1. A row of another
class, another user or another fingerprint is never read; nothing is written.
"""

from typing import Final

from sqlalchemy import text

from classbeat.agent import mix_input
from classbeat.agent.beat_one import MAX_PER_CLASS, Correction
from classbeat.design.music import MusicStep
from classbeat.jobs import JobFailure, JobRun

_READ: Final = text(
    "SELECT b.song, b.beats FROM beat_one_corrections b JOIN jobs j "
    "ON j.class_id = b.class_id AND j.owner_id = b.owner_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND b.owner_id = :owner_id "
    "AND b.fingerprint = :fingerprint ORDER BY b.song LIMIT :limit"
)


async def read(run: JobRun, fingerprint: str) -> tuple[Correction, ...]:
    """The corrections of the job's class for ``fingerprint``, sorted by song."""
    async with run.fenced() as fs:
        rows = (
            await fs.db.execute(
                _READ,
                {
                    "job_id": fs.lease.job_id,
                    "owner_id": fs.lease.owner_id,
                    "fingerprint": fingerprint,
                    "limit": MAX_PER_CLASS,
                },
            )
        ).all()
    try:
        return tuple(Correction(song=int(r.song), beats=int(r.beats)) for r in rows)
    except ValueError:  # the table's checks make this impossible: fail closed
        raise JobFailure("internal") from None


async def attach(run: JobRun, step: MusicStep) -> MusicStep:
    """``step`` with the class's corrections for its fingerprint in ``step.data.beat_one``."""
    found = await read(run, step.fingerprint)
    step.data = mix_input.with_corrections(step.data, found)
    return step
