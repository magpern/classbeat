"""A caller's own reads and writes inside the jobs API's create transaction (ADR 0029 section 5).

A route that enqueues a job on behalf of a stored state (the design-from-interpretation hand-off)
must read that state, create the job and record a refusal as **one** step, so two concurrent
calls cannot see different answers. ``classbeat.jobs.service.create_held`` runs the hold in the
create transaction, under the owner lock, which it takes first:

- ``before(db)``: after the owner lock, before ``user.create``. A ``Held`` ends the call with that
  refusal and creates nothing (a stored refusal, or a state that is not ready); ``None`` goes on;
- ``refused(db, reason, counts)``: after ``user.create`` refused (its own reasons, ``admit``'s, a
  quota refusal or a backstop constraint), with whatever it wrote rolled back to a savepoint. It
  returns what the caller answers, normally the refusal it stored.

An accepted or repeated creation calls neither again. Nothing here opens a session.
"""

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class Held:
    reason: str
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])


class CreateHold(Protocol):
    def before(self, db: AsyncSession, /) -> Awaitable[Held | None]: ...

    def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Awaitable[Held]: ...
