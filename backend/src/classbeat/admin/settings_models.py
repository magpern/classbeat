"""The request and response schemas of the admin Settings routes (``classbeat.admin.settings``):
pure pydantic models, no database and no request."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from classbeat.admin.plan_rules import GATES, LIMITS, PRICES


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonOut(Out):
    id: UUID
    email: str | None


class FlagOut(Out):
    key: str
    source: str  # environment or database
    effective: Any
    environment: Any
    stored: Any  # the row's value, None when there is none
    stored_valid: bool
    updated_at: datetime | None
    updated_by: PersonOut | None


class PlanOut(Out):
    id: str
    name: str
    rank: int
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    monthly_cost_micros: int
    max_class_minutes: int
    games_allowed: bool
    races_allowed: bool
    ftp_tests_allowed: bool
    # Admin only (billing is off): never in a route outside ``/api/admin``.
    monthly_price_cents: int | None
    intro_price_cents: int | None
    intro_price_until: date | None
    currency: str | None
    features: list[str]
    updated_at: datetime
    updated_by: PersonOut | None


class BoundsOut(Out):
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    monthly_cost_micros: int
    max_class_minutes: int
    trial_days: int


class ReadOnlyOut(Out):
    agent_job_cost_micros: int
    song_job_cost_micros: int
    billing_enabled: bool


class SettingsOut(Out):
    flags: list[FlagOut]
    plans: list[PlanOut]
    bounds: BoundsOut
    read_only: ReadOnlyOut


class SettingIn(In):
    value: Any


class PlanIn(In):
    """Wide schema ranges, so a value the table or the environment refuses reaches the route and
    is refused there with an audit row; anything else (a string for a number, an unknown field)
    is the schema's 422."""

    classes_per_month: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    revisions_per_class: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    concurrent_jobs: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    monthly_cost_micros: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    max_class_minutes: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    games_allowed: StrictBool | None = None
    races_allowed: StrictBool | None = None
    ftp_tests_allowed: StrictBool | None = None
    monthly_price_cents: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    intro_price_cents: StrictInt | None = Field(default=None, ge=-(10**12), le=10**12)
    intro_price_until: date | None = None
    currency: str | None = Field(default=None, max_length=8)

    def asked(self) -> dict[str, Any]:
        """What the edit changes: every limit and gate given a value, and every price named at
        all (``null`` clears a price)."""
        sent = self.model_dump()
        out: dict[str, Any] = {k: sent[k] for k in (*LIMITS, *GATES) if sent[k] is not None}
        out.update({k: sent[k] for k in PRICES if k in self.model_fields_set})
        return out
