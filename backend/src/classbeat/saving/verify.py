"""The read-back of a written program (ADR 0007 section 5, stage ``verify`` of Save and Update),
split out of ``classbeat.saving.kind`` (CLAUDE.md, split on touch). Best effort: any failure leaves
the saved row "saved, not checked"; only an error type is logged.
"""

import logging
from typing import Any
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text

from classbeat import mowl_state
from classbeat.connections import OpenConnection, call_opened
from classbeat.jobs import JobRun

logger = logging.getLogger(__name__)

_STATE = text(
    "UPDATE saved_workouts SET state_hash = :state_hash, updated_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND program_id = :program_id "
    "AND detached_at IS NULL AND state_hash IS NULL"
)


async def verify_program(
    run: JobRun,
    opened: OpenConnection,
    spec: dict[str, Any],
    playlist_id: int,
    class_id: UUID,
    program_id: int,
) -> bool:
    """Read the written program back and, when it holds exactly ``spec`` with ``playlist_id``,
    record its normalized state on the live saved row of ``class_id`` whose state is not yet
    recorded (Save's new row; Update's row, whose state ``on_succeeded`` cleared). Best effort:
    any failure leaves the row "saved, not checked"."""
    try:
        program = await call_opened(
            run.factory,
            run.ctx,
            opened,
            lambda c: mowl_api.get_workout(c, program_id),
        )
        if mowl_state.program_id_of(program) != program_id:
            return False
        if not mowl_state.matches(spec, program, playlist_id=playlist_id):
            return False
        digest = mowl_state.state_hash(mowl_state.normalized_program(program))
    except Exception as exc:  # a failed read leaves the row "saved, not checked"
        logger.info("save not verified", extra={"error_type": type(exc).__name__})
        return False
    async with run.fenced() as fs:
        await fs.db.execute(
            _STATE,
            {
                "state_hash": digest,
                "owner_id": run.lease.owner_id,
                "class_id": class_id,
                "program_id": program_id,
            },
        )
    return True
