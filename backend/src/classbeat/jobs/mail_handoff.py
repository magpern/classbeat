"""The worker's half of the "class ready" mail (ADR 0022 section 4; ME5, #656).

The worker never sends mail (and never loads ``classbeat.mail``: ``tests/test_egress_guard.py``).
It hands the mail to the web process with one ``mail_handoff`` row, written in the job's fenced
transactions only:

- ``note_fresh_analysis(fs)`` in the transaction that stores a fresh mix analysis
  (``design.swedebeat_music``): ``jobs.fresh_analysis`` is set, so the job remembers it across
  attempts (a retry that finds the analysis cached still finishes a job that ran one). A stale
  attempt cannot set it: the update is fenced like every other write.
- ``enqueue_class_ready(fs)`` in the store of the transaction that finishes the job (the design
  kinds that design on a mix, and ``swedebeat_verify_mix``): nothing unless the job ran a fresh
  analysis; nothing when the admin switched ``class_ready`` off or the user opted out of it (the
  web side checks both again before sending); otherwise one row ``(owner, class_ready, job)``
  with the job's class id, ``ON CONFLICT DO NOTHING``, and a ``NOTIFY mail_handoff`` that wakes
  the web process when the transaction commits. A cached mix (the flag never set) adds nothing;
  an attempt that ends any other way (a retry, a failure, a lost lease) commits no row.

The web process takes the row with ``mail_handoff_take`` (``classbeat.emails.handoff``).
"""

from typing import Final

from sqlalchemy import text

from classbeat import auth
from classbeat.flags import effective
from classbeat.jobs.errors import LeaseLost
from classbeat.jobs.fence import FencedSession

KIND: Final = "class_ready"  # the registry's kind id and category (``classbeat.emails``)
CHANNEL: Final = "mail_handoff"

_MARK = text(
    "UPDATE jobs SET fresh_analysis = true "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'"
)
_JOB = text(
    "SELECT fresh_analysis, class_id FROM jobs "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'"
)
_INSERT = text(
    "INSERT INTO mail_handoff (owner_id, kind, job_id, params) "
    "VALUES (:owner_id, :kind, :job_id, jsonb_build_object('class_id', CAST(:class_id AS text))) "
    "ON CONFLICT (owner_id, kind, job_id) DO NOTHING"
)
_NOTIFY = text(f"NOTIFY {CHANNEL}")


async def note_fresh_analysis(fs: FencedSession) -> None:
    """The job ran a fresh mix analysis (inside the transaction that stores it)."""
    result = await fs.db.execute(_MARK, fs.lease.params())
    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        raise LeaseLost


async def _wanted(fs: FencedSession) -> bool:
    """The admin switch and the user's preference, read in the same transaction."""
    disabled = await effective.mail_kinds_disabled(fs.db, frozenset({KIND}))
    if KIND in disabled:
        return False
    prefs = await auth.get_mail_prefs(fs.db, fs.lease.owner_id)
    return prefs is not None and not prefs.unsubscribed_all and KIND not in prefs.opted_out


async def enqueue_class_ready(fs: FencedSession) -> bool:
    """In the finishing transaction: hand a "class ready" mail to the web process if the job ran
    a fresh analysis and the mail is wanted; ``True`` if a row was written."""
    row = (await fs.db.execute(_JOB, fs.lease.params())).first()
    if row is None:
        raise LeaseLost
    if not row.fresh_analysis or not await _wanted(fs):
        return False
    params = {
        "owner_id": fs.lease.owner_id,
        "kind": KIND,
        "job_id": fs.lease.job_id,
        "class_id": str(row.class_id),
    }
    written = await fs.db.execute(_INSERT, params)
    if not written.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
        return False
    await fs.db.execute(_NOTIFY)
    return True
