"""The plan edit's pure rules (ADR 0011 section 4; #496): the table's ranges, the environment's
upper bounds, which edits are refused and the audit params of an edit. No database, no request:
``classbeat.admin.settings`` calls these inside its ``admin_session``."""

import re
from datetime import date
from typing import Any

from classbeat.config import Settings

HARD_BOUNDS = {  # the plans table's checks (migration 0007)
    "classes_per_month": (0, 10_000),
    "revisions_per_class": (0, 200),
    "concurrent_jobs": (1, 16),
    "monthly_cost_micros": (0, 10_000_000_000),
    "max_class_minutes": (10, 180),  # migration 0026, the brief's own range
}
LIMITS = tuple(HARD_BOUNDS)
GATES = ("games_allowed", "races_allowed", "ftp_tests_allowed")
PRICE_CENTS = ("monthly_price_cents", "intro_price_cents")
PRICES = (*PRICE_CENTS, "intro_price_until", "currency")
PRICE_RANGE = (0, 10_000_000)  # migration 0026
INTRO_UNTIL_RANGE = (date(2020, 1, 1), date(2100, 12, 31))
CURRENCY = re.compile(r"[A-Z]{3}")


def bounds(settings: Settings) -> dict[str, int]:
    """The most a plan may store: the table's range under the environment's bound (ADR 0011
    section 4: ``CLASSBEAT_JOBS_PER_USER`` is the hard upper bound for every plan)."""
    return {
        "classes_per_month": min(
            HARD_BOUNDS["classes_per_month"][1], settings.classes_per_month_max
        ),
        "revisions_per_class": min(
            HARD_BOUNDS["revisions_per_class"][1], settings.revisions_per_class
        ),
        "concurrent_jobs": min(HARD_BOUNDS["concurrent_jobs"][1], settings.jobs_per_user),
        "monthly_cost_micros": min(
            HARD_BOUNDS["monthly_cost_micros"][1], settings.monthly_cost_micros_max
        ),
        "max_class_minutes": min(HARD_BOUNDS["max_class_minutes"][1], settings.class_minutes_max),
    }


def plan_params(asked: dict[str, Any]) -> dict[str, Any]:
    """The audit params of a plan edit: numbers, gates, an ISO date and a currency code; a
    currency that is not three capital letters (free text) is left out."""
    params: dict[str, Any] = {}
    cleared = [name for name in PRICES if name in asked and asked[name] is None]
    for name, value in asked.items():
        if value is None or (name == "currency" and not CURRENCY.fullmatch(value)):
            continue
        params[name] = value.isoformat() if isinstance(value, date) else value
    if cleared:
        params["cleared"] = cleared
    return params


def plan_refusal(asked: dict[str, Any], bounds: dict[str, int]) -> str | None:
    """``out_of_range`` for a value the table refuses, ``above_environment_bound`` for a limit
    within the table's range but over the environment's bound, ``invalid_value`` for a currency
    that is not three capital letters; ``nothing_to_change`` for none."""
    if not asked:
        return "nothing_to_change"
    for name, value in asked.items():
        if name in LIMITS:
            low, high = HARD_BOUNDS[name]
            if not low <= value <= high:
                return "out_of_range"
            if value > bounds[name]:
                return "above_environment_bound"
        elif name in PRICE_CENTS and value is not None:
            if not PRICE_RANGE[0] <= value <= PRICE_RANGE[1]:
                return "out_of_range"
        elif name == "intro_price_until" and value is not None:
            if not INTRO_UNTIL_RANGE[0] <= value <= INTRO_UNTIL_RANGE[1]:
                return "out_of_range"
        elif name == "currency" and value is not None and not CURRENCY.fullmatch(value):
            return "invalid_value"
    return None


def prices_complete(merged: dict[str, Any]) -> bool:
    """The table's two price checks (migration 0026) on the row as it would be: an introduction
    price comes with its end date, and any price with a currency."""
    if (merged["intro_price_cents"] is None) != (merged["intro_price_until"] is None):
        return False
    priced = merged["monthly_price_cents"] is not None or merged["intro_price_cents"] is not None
    return not priced or merged["currency"] is not None
