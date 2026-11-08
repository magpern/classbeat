"""What an audit row may say (ADR 0011 sections 1, 5 and 6): the action codes, a ``params`` schema
per action, and the limiter that keeps refusal rows from flooding the table.

``admin_audit.params`` is never a secret, code or token: each action declares a pydantic model
(``extra="forbid"``, bounded fields), and ``admin_session`` refuses an action that is not listed
or params its model rejects, before it opens a connection. The admin feature modules (#218,
#220, #221, #222, #223) add their actions to ``ACTIONS``. The free-text validators and types the
feature modules' fields use are in ``classbeat.admin.audit_text``, the refusal-row limiter in
``classbeat.admin.refusal_limiter`` (re-exported here).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
)

from classbeat.admin.refusal_limiter import RefusalLimiter as RefusalLimiter

MAX_PARAMS_BYTES: Final = 2048
OUTCOMES: Final = ("ok", "refused", "error")
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,59}$")]


class Params(BaseModel):
    """The base of every action's params: nothing beyond the declared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


ROUTE_PATTERN: Final = r"^/api/admin[a-z0-9_/{}.-]{0,90}$"
METHOD_PATTERN: Final = r"^[A-Z]{3,7}$"


class RequestParams(Params):
    """A refusal at the admin gate (``admin.request``): which route, which method."""

    method: Annotated[str, StringConstraints(pattern=METHOD_PATTERN)]
    route: Annotated[str, StringConstraints(pattern=ROUTE_PATTERN)]


class NoParams(Params):
    """An action whose target says everything (a read or write of one user or row)."""


class UserListParams(Params):
    """The Users list's filter (AD1 #218) and paging. The search text itself is **not** stored: it
    is a prefix of another person's email, which an audit row must not keep after that person
    deletes their account (ADR 0014 section 5). Only that a search was made, and how long."""

    q_length: Annotated[int, Field(ge=1, le=254)] | None = None
    offset: Annotated[int, Field(ge=0, le=10_000)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class GrantParams(Params):
    """An entitlement grant (AD1 #218): what was granted, and whether a note was given (the note
    itself stays on the entitlement)."""

    plan_id: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")] | None = None
    features: Annotated[list[Code], Field(max_length=10)] = []
    ends_at: Annotated[str, StringConstraints(max_length=40)] | None = None
    note: bool = False


class CodesPageParams(Params):
    """A page of the Codes lists (AD2 #220)."""

    offset: Annotated[int, Field(ge=0, le=10_000)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class InviteCreateParams(Params):
    """An invite's lifetime (AD2 #220); never the code."""

    expires_days: Annotated[int, Field(ge=1, le=365)]


class RedeemCodeCreateParams(Params):
    """A redeem code's terms (AD2 #220); never the code, its hash or its label."""

    plan_id: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")] | None = None
    features: Annotated[list[Code], Field(max_length=16)] = []
    grant_days: Annotated[int, Field(ge=1, le=366)]
    max_redemptions: Annotated[int, Field(ge=1, le=1000)]
    expires_at: Annotated[str, StringConstraints(max_length=40)]


class AuditListParams(Params):
    """The audit log's filter (AD4 #222, ``classbeat.admin.audit_routes``): ids, codes, the
    period and the page. A filter on a person (``actor_id``, ``actor_email``, ``target_user_id``,
    ``target_id``) is stored only as ``<name>_set``: the value could be anyone's id or address,
    and an audit row outlives that person's account (ADR 0014 section 5)."""

    actor_id_set: Literal[True] | None = None
    actor_email_set: Literal[True] | None = None
    action: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,59}$")] | None = None
    target_user_id_set: Literal[True] | None = None
    target_kind: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")] | None = None
    target_id_set: Literal[True] | None = None
    outcome: Literal["ok", "refused", "error"] | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    before: UUID | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class UsageParams(Params):
    """The Usage and cost view's period (AD3 #221): a UTC month, ``YYYY-MM``."""

    month: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]


class JobsParams(Params):
    """The Jobs view's window for recent failures (AD3 #221), in days."""

    days: Annotated[int, Field(ge=1, le=90)]


class AbuseParams(Params):
    """The Abuse view's window (#632), in minutes."""

    minutes: Annotated[int, Field(ge=5, le=240)]


class RefundParams(Params):
    """An admin refund (AD3 #221): the target (user, ledger row) says everything."""


PlanCode = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")]


class SettingSetParams(Params):
    """A flag write (AD4 #222): the key and what the value means, never free text. A banner's
    text stays in ``app_settings`` (only its level and length are logged); a refused value logs
    the key alone."""

    key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{0,39}$")]
    value: Literal["invite", "closed", "off", "granted", "on"] | None = None
    plan: PlanCode | None = None
    days: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    level: Literal["info", "warning"] | None = None
    text_length: Annotated[int, Field(ge=0, le=100_000)] | None = None
    # ``mail_kinds_disabled`` (#653): the kind ids switched off, never text. The mail branding keys
    # log their length only (``text_length``), as the banner does.
    kinds: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{0,39}$")]] | None = Field(
        default=None, max_length=100
    )


class MailTestParams(Params):
    """A test send from the Mail page (#653): the kind id; the recipient is the admin's own
    address and is never logged."""

    kind: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{0,39}$")]


PlanCleared = Literal[
    "monthly_price_cents", "intro_price_cents", "intro_price_until", "currency",
    "saves_per_hour", "saves_per_day",
]  # fmt: skip


class PlanUpdateParams(Params):
    """A plan edit (AD4 #222, #496, #809): the limits, gates, prices and save caps asked for
    (refused ones included), the prices and save caps cleared by name; a currency only as three
    capital letters, a date only as a date, so no free text."""

    classes_per_month: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    revisions_per_class: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    concurrent_jobs: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    monthly_cost_micros: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    max_class_minutes: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    games_allowed: StrictBool | None = None
    races_allowed: StrictBool | None = None
    ftp_tests_allowed: StrictBool | None = None
    monthly_price_cents: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    intro_price_cents: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    intro_price_until: date | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    saves_per_hour: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    saves_per_day: Annotated[int, Field(ge=-(10**12), le=10**12)] | None = None
    cleared: list[PlanCleared] | None = None


MODEL_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"


class ModelRouteParams(Params):
    """A model route write (AD5 #223): which route and what it was set to. Every field is
    pattern-checked before the audit row, so a refusal can say what was asked without free text."""

    route_key: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]
    stage_group: Literal["design", "songs"]
    provider: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,29}$")] | None = None
    model: (
        Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")] | None
    ) = None
    effort: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,19}$")] | None = None


# Action code -> its params model. ``admin.request`` is the gate's own (ADR 0011 section 6:
# mfa_required, stepup_required and rate_limited of an admin).
ACTIONS: dict[str, type[Params]] = {
    "admin.request": RequestParams,
    # Users (AD1 #218, classbeat.admin.users).
    "user.list": UserListParams,
    "user.read": NoParams,
    "user.grant": GrantParams,
    "user.revoke": NoParams,
    "user.disable": NoParams,
    "user.enable": NoParams,
    "user.delete": NoParams,
    # Codes (AD2 #220, classbeat.admin.codes).
    "invite.list": CodesPageParams,
    "invite.create": InviteCreateParams,
    "invite.disable": NoParams,
    "code.list": CodesPageParams,
    "code.create": RedeemCodeCreateParams,
    "code.disable": NoParams,
    # Usage, cost and jobs; the admin refund (AD3 #221, classbeat.admin.usage and .jobs).
    "usage.read": UsageParams,
    "job.list": JobsParams,
    "abuse.list": AbuseParams,
    "refund.credit": RefundParams,
}
# The audit log (AD4 #222).
ACTIONS["audit.list"] = AuditListParams
# Settings, flags and plan limits (AD4 #222, classbeat.admin.settings).
ACTIONS["setting.read"] = NoParams
ACTIONS["setting.set"] = SettingSetParams
ACTIONS["setting.clear"] = NoParams
ACTIONS["plan.update"] = PlanUpdateParams
# The Mail page (#653, classbeat.admin.mail): the branding and switches are read and written with
# ``setting.read``/``setting.set``-style rows of their own; a preview and a test send log the kind.
ACTIONS["mail.read"] = NoParams
ACTIONS["mail.preview"] = MailTestParams
ACTIONS["mail.test"] = MailTestParams
# Model routes per route key (AD5 #223, classbeat.admin.models).
ACTIONS["model.read"] = NoParams
ACTIONS["model.route_set"] = ModelRouteParams
ACTIONS["model.route_clear"] = ModelRouteParams


class UnknownAction(ValueError):
    """An action that is not in ``ACTIONS`` or params its model rejects (a bug, not a refusal)."""


def checked_params(action: str, params: Mapping[str, Any] | None) -> str:
    """The JSON stored in ``admin_audit.params`` for ``action``; raises ``UnknownAction``."""
    model = ACTIONS.get(action)
    if model is None:
        raise UnknownAction(f"unknown admin action {action!r}")
    try:
        value = model.model_validate(dict(params or {}))
    except ValidationError:
        raise UnknownAction(f"params rejected for admin action {action!r}") from None
    dumped = value.model_dump(mode="json")
    if _holds_nul(dumped):  # jsonb refuses a NUL in a string: the row could never be written
        raise UnknownAction(f"params rejected for admin action {action!r}")
    encoded = json.dumps(dumped, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode()) > MAX_PARAMS_BYTES:
        raise UnknownAction(f"params too large for admin action {action!r}")
    return encoded


def _holds_nul(value: object) -> bool:
    """Whether a dumped value has a real NUL character in a key or string. A literal backslash
    followed by ``u0000`` is six ordinary characters, which ``jsonb`` stores fine."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        items: dict[object, object] = value  # pyright: ignore[reportUnknownVariableType]
        return any(_holds_nul(k) or _holds_nul(v) for k, v in items.items())
    if isinstance(value, list):
        values: list[object] = value  # pyright: ignore[reportUnknownVariableType]
        return any(_holds_nul(v) for v in values)
    return False


@dataclass(frozen=True, slots=True)
class Target:
    """What an action is about: a user (``user_id``, nulled when the account goes), and a kind
    with an id kept as text (a deleted user's id survives in ``target_id``, ADR 0011 s. 6)."""

    user_id: UUID | None = None
    kind: str | None = None
    id: str | None = None
