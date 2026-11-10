"""A kind's ``admit`` bound to one parsed input, as ``user.create`` runs it (ADR 0005 section 2,
ADR 0007 section 3). Split out of ``classbeat.jobs.routes``; nothing here opens a session.

What ``admit`` accepts is validated by the kind's model again (with its server fields) and stored
in place of the browser's input; a stored input over ``max_bytes`` is refused ``too_large``.
"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.http import canonical_json
from classbeat.jobs.inputs import jsonb_size
from classbeat.jobs.kinds import JobKind, Refused
from classbeat.jobs.user import Admission, CreateAdmit

INPUT_MAX_BYTES = 16384  # jobs.input's check (migration 0004), measured as jsonb prints it


def bound_admit(
    kind: JobKind, parsed: BaseModel, *, max_bytes: int = INPUT_MAX_BYTES
) -> CreateAdmit | None:
    """The kind's ``admit`` for this input, run by ``user.create`` under the owner lock."""
    admit = kind.admit
    if admit is None:
        return None

    async def run(db: AsyncSession, ctx: UserContext) -> Admission | Refused:
        result = await admit(db, ctx, parsed)
        if isinstance(result, Refused):
            return result
        stored = kind.admitted_input(parsed, result.fields).model_dump(mode="json")
        if jsonb_size(stored) > max_bytes:
            return Refused("too_large")
        return Admission(canonical_json(stored).decode("utf-8"), result.after_insert)

    return run
