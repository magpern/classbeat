"""A new class's title from the interpreter's short name (issue #1233).

``interpret_request`` creates its class with the title the client sent or, when it sent none, the
request's first words (``interpret_policy.title_for``). In the job's final fenced transaction,
``adopt`` replaces that title with the reading's ``name`` (already cleaned by
``class_title_policy.proposed_name`` in ``InterpretedRequest.sanitised``) **only while the class's
title is still the request's first words**: a title the user chose, or any other title, is never
overwritten, and with no name nothing is written. The name is display text only: nothing decides
on it, and it never goes back to a model.

The statement filters on the job's own class and ``owner_id`` as well as running under RLS.
"""

from typing import Final

from sqlalchemy import text

from classbeat.jobs import FencedSession

_ADOPT: Final = text(
    "UPDATE classes c SET title = :name FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.title = :from_request RETURNING c.id"
)


async def adopt(fs: FencedSession, name: str | None, from_request: str) -> bool:
    """Whether the class now has ``name`` as its title (see the module docstring)."""
    if name is None or name == from_request:
        return False
    params = {
        "name": name,
        "from_request": from_request,
        "job_id": fs.lease.job_id,
        "owner_id": fs.lease.owner_id,
    }
    return (await fs.db.execute(_ADOPT, params)).first() is not None
