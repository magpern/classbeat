"""The discard of a failed first creation's empty class (ADR 0039), in the ending's transaction.

``discard_first_creation(db, owner_id, job_id)`` runs in the transaction that ends the job, after
the job row is ``failed`` or ``cancelled`` and while that row is locked: the worker's fenced
terminal transition (``classbeat.jobs.transitions``) and the user's cancel of a queued job
(``classbeat.jobs.service.cancel``). It

1. reads the ending job; anything ``discard_policy.candidate`` does not name returns at once, with
   no class lock;
2. locks the class row (``LOCK_CLASS``, as the class delete does): no job, revision, operation or
   other row of the class can be inserted until the transaction ends (their foreign keys wait on
   the lock), and the facts below are read after it, so they include what committed before;
3. reads the facts and asks ``discard_policy.may_discard`` (an allowlist);
4. detaches the ending job (``class_id`` NULL, ``discarded_class_id`` the class, ``input`` emptied
   so no request text outlives the class, a fresh idempotency key so the same request starts a
   new class instead of replaying this job) and deletes the class (``DELETE_CLASS``, the class
   delete's statement; the schema's cascade removes the reading and the messages).

The detached job stays readable as the owner's (``GET /api/jobs/{id}``: its state, category and
events), keeps its credit for the settle step (ADR 0011 section 3) and its ``agent_runs`` (ADR 0007
section 10). Run again for the same job (a replay, a second settle), it finds no class and does
nothing. Only the owner's rows are read and written (``owner_id`` and RLS).
"""

import uuid
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes.class_sql import DELETE_CLASS, LOCK_CLASS
from classbeat.jobs.discard_policy import ENDINGS, Facts, candidate, may_discard

__all__ = ["DISCARDED_WITH", "ENDINGS", "KEEPING", "KEEPING_BY_JOB", "discard_first_creation"]

# Every table whose row for the class keeps it: a saved workout, an external write record (ADR
# 0005 section 7, which must keep blocking), a playlist, a hand-off, a correction, an output, a
# sync or a player publication; by another job of the class (which goes with it), an external
# operation or an owed mail; by the ending job (which stays), an owed mail or an external
# operation still ``pending`` or ``uncertain`` (a finished one, such as the music stage's MOWL
# attach, stays with the job). ``tests/test_jobs_discard_schema.py`` checks that every table
# naming a class or a job is in one of these or in ``DISCARDED_WITH``.
KEEPING: Final = (
    ("saved_workouts", "class_id"),
    ("external_operations", "class_id"),
    ("class_playlists", "class_id"),
    ("swedebeat_handoffs", "class_id"),
    ("beat_one_corrections", "class_id"),
    ("class_outputs", "class_id"),
    ("spotify_syncs", "class_id"),
    ("player_publications", "source_class_id"),
)
KEEPING_BY_JOB: Final = ("external_operations", "mail_handoff")
# Rows that go with the discarded class or its reading (or outlive it, as the usage does); a
# pending chat message (ADR 0038 section 8) is chat, like ``class_messages``.
DISCARDED_WITH: Final = frozenset(
    {
        "jobs",
        "class_messages",
        "class_pending_messages",
        "class_revisions",
        "job_events",
        "agent_runs",
        "credit_ledger",
    }
)

_JOB = text(
    "SELECT kind, state, class_id, credit_state, idempotency_key FROM jobs "
    "WHERE id = :job_id AND owner_id = :owner_id"
)
_KEPT = " OR ".join(
    [
        f"EXISTS (SELECT 1 FROM {table} k WHERE k.owner_id = :owner_id AND k.{column} = :class_id)"  # noqa: S608 - constants only
        for table, column in KEEPING
    ]
    + [
        f"EXISTS (SELECT 1 FROM {table} k JOIN jobs kj ON kj.owner_id = k.owner_id "  # noqa: S608 - constants only
        "AND kj.id = k.job_id WHERE k.owner_id = :owner_id AND kj.class_id = :class_id "
        "AND kj.id <> :job_id)"
        for table in KEEPING_BY_JOB
    ]
    + [
        "EXISTS (SELECT 1 FROM mail_handoff k WHERE k.owner_id = :owner_id AND k.job_id = :job_id)",
        "EXISTS (SELECT 1 FROM external_operations k WHERE k.owner_id = :owner_id "
        "AND k.job_id = :job_id AND k.state IN ('pending', 'uncertain'))",
    ]
)
# The reading's hand-off key is ``design.interpretation_row.handoff_key`` ("interpret-" and the
# reading job's id as hex), which ``tests/test_jobs_discard.py`` checks against this statement.
_FACTS = text(
    "SELECT "  # noqa: S608 - constants only
    "(SELECT c.interpretation IS NOT NULL FROM classes c "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id) AS has_reading, "
    "(SELECT count(*) FROM class_revisions r "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id) AS revisions, "
    "(SELECT count(*) FROM jobs o WHERE o.owner_id = :owner_id AND o.class_id = :class_id "
    "AND o.id <> :job_id) AS other_jobs, "
    "(SELECT count(*) FROM jobs o WHERE o.owner_id = :owner_id AND o.class_id = :class_id "
    "AND o.id <> :job_id AND o.kind = 'interpret_request' AND o.state = 'completed' "
    "AND :key = 'interpret-' || replace(o.id::text, '-', '')) AS readings, "
    f"({_KEPT}) AS kept"
)
_DETACH = text(
    "UPDATE jobs SET class_id = NULL, discarded_class_id = :class_id, input = '{}'::jsonb, "
    "idempotency_key = :key, updated_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND class_id = :class_id "
    "AND state IN ('failed', 'cancelled')"
)


async def discard_first_creation(db: AsyncSession, owner_id: UUID, job_id: UUID) -> UUID | None:
    """The id of the class this ending discarded, or ``None`` when the class stays (or is gone)."""
    job = (await db.execute(_JOB, {"job_id": job_id, "owner_id": owner_id})).first()
    if job is None or job.class_id is None or not candidate(job.kind, job.state):
        return None
    ids = {"owner_id": owner_id, "class_id": job.class_id}
    locked = (await db.execute(LOCK_CLASS, ids)).first()
    if locked is None:
        return None
    found = (await db.execute(_FACTS, {**ids, "job_id": job_id, "key": job.idempotency_key})).one()
    facts = Facts(
        kind=job.kind,
        state=job.state,
        credit_state=job.credit_state,
        has_version=locked.current_revision_id is not None
        or locked.approved_swedebeat_match_id is not None,
        has_reading=bool(found.has_reading),
        revisions=int(found.revisions),
        other_jobs=int(found.other_jobs),
        readings=int(found.readings),
        kept=bool(found.kept),
    )
    if not may_discard(facts):
        return None
    key = f"discarded-{uuid.uuid4().hex}"
    detached = await db.execute(_DETACH, {**ids, "job_id": job_id, "key": key})
    if detached.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        return None  # not reached: the caller holds the job row
    await db.execute(DELETE_CLASS, ids)
    return job.class_id
