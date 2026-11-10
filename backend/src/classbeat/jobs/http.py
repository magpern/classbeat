"""Pieces of the jobs HTTP API that signed-in routes share.

Nothing here opens a session, and this module does not import ``classbeat.jobs.service``.
The session guard allows that module only from ``classbeat.jobs.routes``.
"""

import json
import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import quota
from classbeat.context import UserContext
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import CreateHold
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import JobKind
from classbeat.jobs.user import JobLimitPolicy

Factory = async_sessionmaker[AsyncSession]

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def canonical_json(value: Any) -> bytes:
    """The canonical JSON of ADR 0004 section 2 (sorted keys, no spaces, UTF-8): the stored
    input and the request hash. Not imported from ``classbeat.crypto`` (guarded there)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def credit_ledger(request: Request) -> CreditLedger:
    """The credit seam (ADR 0005 section 5): M5's metered ledger (ADR 0011 section 3)."""
    return quota.MeteredLedger(quota.Bounds.from_settings(request.app.state.settings))


def job_limit(request: Request) -> JobLimitPolicy:
    """The concurrent-job limit: the plan's, under ``CLASSBEAT_JOBS_PER_USER`` (section 4)."""
    return quota.PlanJobLimitPolicy(request.app.state.settings.jobs_per_user)


@dataclass(frozen=True, slots=True)
class Enqueued:
    """A job created for the signed-in user, or the refusal (``job_id`` is none)."""

    job_id: UUID | None
    created: bool
    reason: str | None = None
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])
    state: str | None = None  # the job's state when it was read, in the creating transaction


class JobEnqueue(Protocol):
    """``classbeat.jobs.routes.enqueue``. The web app passes it to the hand-off routes."""

    def __call__(
        self,
        factory: Factory,
        ctx: UserContext,
        parsed: JobInput,
        kind: JobKind,
        *,
        idempotency_key: str,
        ledger: CreditLedger,
        limit: JobLimitPolicy,
        consumes_credit: bool,
        hold: CreateHold | None = None,
    ) -> Awaitable[Enqueued]: ...
