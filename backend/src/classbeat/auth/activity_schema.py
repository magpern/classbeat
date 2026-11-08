"""The response models of ``GET /api/auth/events`` (#678; ``activity`` holds the query)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ActivityItemOut(BaseModel):
    id: UUID
    kind: str
    at: datetime
    ip: str | None
    provider: str | None  # google, github or microsoft, for the kinds that record one
    other_session: bool  # a sign-out made from another session's list
    passkey: bool  # a sign-in or re-authentication (or its failure) with a passkey (ADR 0019)


class ActivityOut(BaseModel):
    events: list[ActivityItemOut]
    next_before: str | None  # pass as ``before`` for the next, older page; null at the end
