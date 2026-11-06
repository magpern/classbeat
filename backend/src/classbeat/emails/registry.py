"""One entry per kind of mail (ADR 0022 section 1).

The send gate, the admin page (ME2), account preferences (ME4) and the preview list read this;
``tests/test_emails_registry.py`` fails if an entry lacks a field, a golden file, sample params or
catalogue strings. An *essential* kind is never switched off by the admin or the user.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from classbeat.emails import kinds
from classbeat.emails.kinds import Builder


class Category(StrEnum):
    ESSENTIAL = "essential"
    WELCOME = "welcome"
    ACCOUNT_ACTIVITY = "account_activity"
    CLASS_READY = "class_ready"


# The fixed time in sample params, so previews and golden files are stable.
SAMPLE_WHEN = datetime(2026, 11, 6, 12, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Kind:
    id: str
    category: Category
    essential: bool
    user_can_opt_out: bool
    admin_can_disable: bool
    enabled_by_default: bool
    builder: Builder
    sample: Mapping[str, Any] = field(default_factory=lambda: {})


def _essential(kind_id: str, builder: Builder, sample: Mapping[str, Any]) -> Kind:
    return Kind(kind_id, Category.ESSENTIAL, True, False, False, True, builder, sample)


def _activity(kind_id: str, builder: Builder, sample: Mapping[str, Any]) -> Kind:
    return Kind(kind_id, Category.ACCOUNT_ACTIVITY, False, True, True, True, builder, sample)


_TOKEN = {"token": "SAMPLE"}
_WHEN = {"when": SAMPLE_WHEN}

_ALL = (
    _essential("signup_confirm", kinds.signup_confirm, _TOKEN),
    _essential(
        "provider_signup_confirm",
        kinds.provider_signup_confirm,
        {"provider": "google", **_TOKEN},
    ),
    _essential("password_reset", kinds.password_reset, _TOKEN),
    _essential("email_change_confirm", kinds.email_change_confirm, _TOKEN),
    _essential("account_exists", kinds.account_exists, {}),
    _essential("email_change_taken", kinds.email_change_taken, {}),
    _essential(
        "connections_wiped",
        kinds.connections_wiped,
        {"providers": ("mowl", "spotify", "swedebeat"), "reset_token": "SAMPLE"},
    ),
    _essential("email_changed", kinds.email_changed, _WHEN),
    _activity("totp_enrolled", kinds.totp_enrolled, _WHEN),
    _activity("mfa_failures", kinds.mfa_failures, _WHEN),
    _activity("identity_linked", kinds.identity_linked, {"provider": "github", **_WHEN}),
)

REGISTRY: dict[str, Kind] = {k.id: k for k in _ALL}


def switchable_categories() -> tuple[str, ...]:
    """The categories a user can switch off (ME4): every one but essential, whether or not a kind
    of it exists yet, so a choice made now still holds when ME5 and the class mails arrive."""
    return tuple(c.value for c in Category if c is not Category.ESSENTIAL)
