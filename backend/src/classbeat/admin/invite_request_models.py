"""The shapes of the admin's invite requests (ADR 0033 section 3): bodies, answers and the audit
params of ``invite_request.*``, registered in ``ACTIONS`` here (as ``classbeat.admin.billing``
does). An audit row names the request (its target) and the terms, never the address, the text,
the code or its hash.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from classbeat.admin.audit import ACTIONS, NoParams, Params

# How long an issued invite (and its tier's code) lives, in days: the admin's choice, at most 30,
# the retention of a decided request (``auth.invite_request_policy``), so the request outlives it.
EXPIRES_DAYS_DEFAULT = 14
EXPIRES_DAYS_MAX = 30

State = Literal["pending", "issued", "declined"]
PlanId = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")]
Delivery = Literal["queued", "mail_off", "not_requested"]


class ListParams(Params):
    state: State | None = None
    offset: Annotated[int, Field(ge=0, le=10_000)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class ApproveParams(Params):
    plan_id: PlanId | None = None
    grant_days: Annotated[int, Field(ge=1, le=366)] | None = None
    expires_days: Annotated[int, Field(ge=1, le=EXPIRES_DAYS_MAX)]
    send_email: bool


ACTIONS["invite_request.list"] = ListParams
ACTIONS["invite_request.approve"] = ApproveParams
ACTIONS["invite_request.decline"] = NoParams  # one row per request, the bulk decline's too

# The bulk decline's bound (#989): one request, at most this many audited transactions.
DECLINE_MAX = 100


class ApproveIn(BaseModel):
    """The tier is a plan and its days (both or neither: neither is a plain invite)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: PlanId | None = None
    grant_days: Annotated[int, Field(ge=1, le=366)] | None = None
    expires_days: Annotated[int, Field(ge=1, le=EXPIRES_DAYS_MAX)] = EXPIRES_DAYS_DEFAULT
    send_email: StrictBool = False

    @model_validator(mode="after")
    def _tier_is_whole(self) -> "ApproveIn":
        if (self.plan_id is None) != (self.grant_days is None):
            raise ValueError("a tier names a plan and its grant_days")
        return self


class DeclineIn(BaseModel):
    """The requests to decline: ids only, never an address or text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ids: Annotated[list[UUID], Field(min_length=1, max_length=DECLINE_MAX)]


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class PersonOut(Out):
    id: UUID
    email: str | None  # None: the account is gone


class InviteOut(Out):
    id: UUID
    state: str  # active, used or expired


class RequestOut(Out):
    id: UUID
    email: str
    heard_from: str  # the visitor's text: show it as text, never as HTML
    instructor: bool
    locale: str | None
    has_account: bool  # the address is already an account's (only the admin sees this)
    state: State
    plan_id: str | None
    grant_days: int | None
    invite: InviteOut | None  # None: not issued, or the invite was purged
    decided_by: PersonOut | None
    decided_at: datetime | None
    created_at: datetime


class RequestPage(Out):
    requests: list[RequestOut]
    next_offset: int | None
    pending: int  # the whole pending queue, counted by the server whatever the page (#989)


class Declined(Out):
    declined: list[UUID]
    skipped: list[UUID]  # gone, or decided meanwhile (each has its ``refused`` audit row)


class Issued(Out):
    code: str  # shown once; only its hash is stored
    request: RequestOut
    email: Delivery
