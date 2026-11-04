"""Make a verified mix the class's design (ADR 0016 section 7, the hand-off into A4).

Called from ``design_draft``'s final transaction, after the draft revision is stored. A job the
hand-off started (``handoff_id`` set) becomes the class's current revision and its music source
only when the verified row still names this mix and the stored match still belongs to the
current draft's song list. Anything else fails the job ``invalid_input`` and rolls the revision
back. A design that did not come from a hand-off is left as ``adopt_first`` stored it. The
compare-and-set never writes over a ``none`` source (a soundless class, ADR 0007 amendment of
2026-11-03, item 9; #530): such a class fails the job ``invalid_input`` too.
"""

import json
from uuid import UUID

from sqlalchemy import text

from classbeat.classes.selection import stored_approval
from classbeat.jobs import FencedSession, JobFailure

_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")
_ROW = text(
    "SELECT id, match_revision_id FROM swedebeat_handoffs "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND state = 'verified' "
    "AND mix_id = :mix_id AND superseded_at IS NULL AND expires_at > now()"
)
_ADOPT = text(
    "UPDATE classes SET current_revision_id = :revision_id, "
    "music_source = CAST(:music AS jsonb), updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :class_id AND current_revision_id = :expected "
    # Never over a soundless class's ``none`` source (#530): no row, ``invalid_input``.
    "AND (music_source IS NULL OR music_source ->> 'source' IS DISTINCT FROM 'none') "
    "RETURNING id"
)


async def adopt_verified_mix(
    fs: FencedSession, revision_id: UUID, mix_id: str, *, handoff_id: UUID | None
) -> None:
    """Switch the class onto the mix when this job is that hand-off's design."""
    if handoff_id is None:
        return
    owner = fs.lease.owner_id
    class_id = (
        await fs.db.execute(_CLASS, {"job_id": fs.lease.job_id, "owner_id": owner})
    ).scalar_one()
    row = (
        await fs.db.execute(
            _ROW,
            {"owner_id": owner, "class_id": class_id, "mix_id": mix_id},
        )
    ).first()
    if row is None or row.id != handoff_id:
        raise JobFailure("invalid_input")
    bound, _reason = await stored_approval(fs.db, owner, class_id)
    if bound is None or bound.revision_id != row.match_revision_id:
        raise JobFailure("invalid_input")
    music = json.dumps({"mix_id": mix_id, "source": "mowl_swedebeat_mix"}, sort_keys=True)
    adopted = (
        await fs.db.execute(
            _ADOPT,
            {
                "revision_id": revision_id,
                "music": music,
                "owner_id": owner,
                "class_id": class_id,
                "expected": bound.current_revision_id,
            },
        )
    ).first()
    if adopted is None:
        raise JobFailure("invalid_input")
