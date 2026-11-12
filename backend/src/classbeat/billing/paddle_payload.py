"""Strict readers for a Paddle notification's JSON (ADR 0032 section 1). Pure: no I/O.

Used by `paddle_events` only, after the signature has been verified. Every reader raises
`PayloadError` with a fixed reason code and never the value it read, so a refusal can be logged
without any of the body.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

# Paddle ids are a short type prefix and 26 lowercase base32 characters (`txn_01h04v...`).
_ID: Final = re.compile(r"[a-z]{2,5}_[a-z0-9]{1,64}")
_MINOR: Final = re.compile(r"[0-9]{1,15}")
_CURRENCY: Final = re.compile(r"[A-Z]{3}")

Json = Mapping[str, object]


class PayloadError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime


def load(body: bytes) -> Json:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        raise PayloadError("malformed") from None
    if not isinstance(value, dict):
        raise PayloadError("malformed")
    return value  # pyright: ignore[reportUnknownVariableType]


def obj(parent: Json, key: str) -> Json:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise PayloadError("malformed")
    return value  # pyright: ignore[reportUnknownVariableType]


def opt_obj(parent: Json, key: str) -> Json | None:
    return None if parent.get(key) is None else obj(parent, key)


def text(parent: Json, key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value or len(value) > 200:
        raise PayloadError("malformed")
    return value


def ident(parent: Json, key: str) -> str:
    value = text(parent, key)
    if not _ID.fullmatch(value):
        raise PayloadError("malformed")
    return value


def minor(parent: Json, key: str) -> int:
    """An amount in minor units: Paddle sends them as strings of digits (`"1000"`)."""
    value = parent.get(key)
    if not isinstance(value, str) or not _MINOR.fullmatch(value):
        raise PayloadError("malformed")
    return int(value)


def currency(parent: Json, key: str) -> str:
    value = text(parent, key)
    if not _CURRENCY.fullmatch(value):
        raise PayloadError("malformed")
    return value


def moment(parent: Json, key: str) -> datetime:
    """An RFC 3339 time with an offset (`...Z`); a naive time is refused."""
    try:
        value = datetime.fromisoformat(text(parent, key))
    except ValueError:
        raise PayloadError("malformed") from None
    if value.tzinfo is None:
        raise PayloadError("malformed")
    return value.astimezone(UTC)


def period(parent: Json, key: str) -> Period:
    """A `{starts_at, ends_at}` object; `period_missing` when it is absent or null."""
    if parent.get(key) is None:
        raise PayloadError("period_missing")
    found = obj(parent, key)
    start, end = moment(found, "starts_at"), moment(found, "ends_at")
    if not start < end:
        raise PayloadError("malformed")
    return Period(start, end)


def single_item(data: Json) -> Json:
    """The one line item of a subscription or transaction: classbeat sells one plan at a time."""
    items = data.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):  # pyright: ignore[reportUnknownArgumentType]
        raise PayloadError("items_not_single")
    item: Json = items[0]  # pyright: ignore[reportUnknownVariableType]
    quantity = item.get("quantity", 1)
    if type(quantity) is not int or quantity != 1:  # `True == 1` in Python: compare the type
        raise PayloadError("items_not_single")
    return item


def owner(data: Json, key: str) -> UUID:
    """The classbeat user id our checkout put into `custom_data`."""
    custom = data.get("custom_data")
    value = custom.get(key) if isinstance(custom, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(value, str):
        raise PayloadError("owner_missing")
    try:
        return UUID(value)
    except ValueError:
        raise PayloadError("owner_missing") from None
