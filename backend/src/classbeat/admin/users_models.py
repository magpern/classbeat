"""The admin user pages' request and response models (ADR 0011 section 6). Pure: no I/O.

Split from ``classbeat.admin.users`` (#948, split on touch); that module re-imports every name.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from classbeat.admin.audit_text import NoteText, bounded_text
from classbeat.plans.tables import GRANT_FEATURES


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class PlanOut(Out):
    id: str
    name: str
    source: str | None  # None: free without an entitlement
    ends_at: datetime | None


class UserOut(Out):
    id: UUID
    email: str
    created_at: datetime
    role: str
    disabled_at: datetime | None
    plan: PlanOut
    features: list[str]
    classes_held: int
    classes_limit: int
    cost_micros: int


class UserPage(Out):
    users: list[UserOut]
    next_offset: int | None


class EntitlementOut(Out):
    id: UUID
    plan_id: str | None
    features: list[str]
    source: str
    starts_at: datetime
    ends_at: datetime | None
    granted_by: UUID | None
    note: str | None
    revoked_at: datetime | None
    revoked_by: UUID | None
    created_at: datetime
    active: bool


class LedgerOut(Out):
    id: UUID
    job_id: UUID | None
    period: str
    plan_id: str
    state: str
    reason: str | None
    settled_at: datetime | None
    refunded_by: UUID | None
    created_at: datetime


class ConnectionOut(Out):
    provider: str
    status: str
    last_error: str | None
    connected_at: datetime | None
    last_used_at: datetime | None


class UserDetail(UserOut):
    entitlements: list[EntitlementOut]
    ledger: list[LedgerOut]
    connections: list[ConnectionOut]


class DisabledOut(Out):
    id: UUID
    disabled_at: datetime | None
    sessions_revoked: int


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GrantIn(In):
    plan_id: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")] | None = None
    features: Annotated[list[str], Field(max_length=len(GRANT_FEATURES))] = []
    ends_at: AwareDatetime | None = None
    # No control character (``entitlements.note`` refuses a NUL, which was a 500) and no format
    # character (bidi, zero-width): the note is shown to admins again (#227).
    note: NoteText | None = None

    @model_validator(mode="after")
    def _grants_something(self) -> "GrantIn":
        if self.plan_id is None and not self.features:
            raise ValueError("a grant names a plan or a feature")
        if not set(self.features) <= GRANT_FEATURES or len(set(self.features)) != len(
            self.features
        ):
            raise ValueError("features are distinct codes of the feature list")
        return self


class DeleteIn(In):
    email: Annotated[str, StringConstraints(min_length=1, max_length=320), bounded_text(3840)]
