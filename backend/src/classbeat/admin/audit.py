"""What an audit row may say (ADR 0011 sections 1, 5 and 6): the action codes, a ``params`` schema
per action, and the limiter that keeps refusal rows from flooding the table.

``admin_audit.params`` is never a secret, code or token: each action declares a pydantic model
(``extra="forbid"``, bounded fields), and ``admin_session`` refuses an action that is not listed
or params its model rejects, before it opens a connection. The admin feature modules (#218,
#220, #221, #222, #223) add their actions to ``ACTIONS``.
"""

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

logger = logging.getLogger(__name__)

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
    """The Users list's filter (AD1 #218): an email prefix (never a full credential) and paging."""

    q: Annotated[str, StringConstraints(max_length=254)] | None = None
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
}


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
    encoded = json.dumps(value.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    if len(encoded.encode()) > MAX_PARAMS_BYTES:
        raise UnknownAction(f"params too large for admin action {action!r}")
    return encoded


@dataclass(frozen=True, slots=True)
class Target:
    """What an action is about: a user (``user_id``, nulled when the account goes), and a kind
    with an id kept as text (a deleted user's id survives in ``target_id``, ADR 0011 s. 6)."""

    user_id: UUID | None = None
    kind: str | None = None
    id: str | None = None


@dataclass(slots=True)
class RefusalLimiter:
    """Refusal rows per minute (ADR 0011 section 6): at most ``per_admin`` per admin and
    ``total`` overall, in fixed one-minute windows of this process; beyond that a log count.
    Neither the table nor the maint pool (2 connections) can be exhausted by refusals."""

    per_admin: int = 20
    total: int = 100
    clock: Any = time.monotonic
    _window: float = -1.0
    _counts: dict[UUID, int] = field(default_factory=lambda: dict[UUID, int]())
    _total: int = 0
    _dropped: int = 0

    def allow(self, admin_id: UUID) -> bool:
        window = float(int(self.clock() // 60))
        if window != self._window:
            if self._dropped:
                logger.warning("admin refusal rows not written", extra={"dropped": self._dropped})
            self._window, self._counts, self._total, self._dropped = window, {}, 0, 0
        if self._total >= self.total or self._counts.get(admin_id, 0) >= self.per_admin:
            self._dropped += 1
            return False
        self._counts[admin_id] = self._counts.get(admin_id, 0) + 1
        self._total += 1
        return True
