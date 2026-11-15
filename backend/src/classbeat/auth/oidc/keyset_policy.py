"""The signing key set and its rotation rules (ADR 0034 section 1). Pure: no I/O, no crypto.

The secret file ``oidc_signing_keys`` is JSON: ``{"keys": [{"kid", "state", "published_at",
"retired_at", "pem"}]}`` with ``state`` one of ``next``, ``current`` and ``retired``. Exactly one
key is ``current`` (it signs); at most one is ``next``.

- The key set (JWKS) publishes the current key, the next one, and a retired key until 24 hours
  after its ``retired_at`` (its last signature): the player caches the key set for 24 hours.
- ``promote`` makes the next key current only once it has been published for at least 24 hours
  and 5 minutes, and retires the old current key now.
- ``add_next`` adds a new next key (none may exist). On an empty set the new key is current at
  once: no player can have cached a key set that lacks it.
- ``drop_expired`` removes the retired keys no longer published.

Every rule refuses with ``KeysetRefused`` (its message names the rule, never a key).
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

State = Literal["next", "current", "retired"]
PUBLISH_BEFORE_SIGNING = timedelta(hours=24, minutes=5)
PUBLISHED_AFTER_RETIRING = timedelta(hours=24)
_KID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_STATES = ("next", "current", "retired")


class KeysetRefused(ValueError):
    """The key set or a rotation step is not allowed."""


@dataclass(frozen=True, slots=True)
class KeyEntry:
    kid: str
    state: State
    published_at: datetime
    retired_at: datetime | None
    pem: str

    def __repr__(self) -> str:  # never the private key
        return f"KeyEntry(kid={self.kid!r}, state={self.state!r})"


def _time(value: Any, name: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        moment = None
    if moment is None:
        raise KeysetRefused(f"{name} must be an ISO 8601 time")
    if moment.tzinfo is None:
        raise KeysetRefused(f"{name} must carry a time zone")
    return moment


def _entry(raw: Any) -> KeyEntry:
    if not isinstance(raw, dict):
        raise KeysetRefused("every key must be an object")
    kid, state, pem = raw.get("kid"), raw.get("state"), raw.get("pem")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(kid, str) or not _KID.fullmatch(kid):
        raise KeysetRefused("kid must be 1 to 64 letters, digits, '-' or '_'")
    if state not in _STATES:
        raise KeysetRefused("state must be next, current or retired")
    if not isinstance(pem, str) or not pem.startswith("-----BEGIN"):
        raise KeysetRefused("pem must be a PEM private key")
    retired = raw.get("retired_at")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    retired_at = None if retired is None else _time(retired, "retired_at")
    if (state == "retired") != (retired_at is not None):
        raise KeysetRefused("retired_at is set exactly on retired keys")
    return KeyEntry(kid, state, _time(raw.get("published_at"), "published_at"), retired_at, pem)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def check(keys: list[KeyEntry]) -> list[KeyEntry]:
    if len({key.kid for key in keys}) != len(keys):
        raise KeysetRefused("every kid must be unique")
    if sum(key.state == "current" for key in keys) != 1:
        raise KeysetRefused("exactly one key must be current")
    if sum(key.state == "next" for key in keys) > 1:
        raise KeysetRefused("at most one key may be next")
    return keys


def parse(text: str | bytes, *, allow_empty: bool = False) -> list[KeyEntry]:
    try:
        document = json.loads(text)
    except ValueError:
        raise KeysetRefused("the key set must be JSON") from None
    raw = document.get("keys") if isinstance(document, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(raw, list):
        raise KeysetRefused('the key set must be {"keys": [...]}')
    keys = [_entry(item) for item in raw]  # pyright: ignore[reportUnknownVariableType]
    return keys if allow_empty and not keys else check(keys)


def dump(keys: list[KeyEntry]) -> str:
    # The fields in file order; ``default`` writes each time as ISO 8601.
    items = [asdict(key) for key in keys]
    return json.dumps({"keys": items}, indent=2, default=datetime.isoformat) + "\n"


def signing_key(keys: list[KeyEntry]) -> KeyEntry:
    return next(key for key in check(keys) if key.state == "current")


def published(keys: list[KeyEntry], now: datetime) -> list[KeyEntry]:
    return [
        key
        for key in keys
        if key.retired_at is None or now < key.retired_at + PUBLISHED_AFTER_RETIRING
    ]


def add_next(keys: list[KeyEntry], kid: str, pem: str, now: datetime) -> list[KeyEntry]:
    if not _KID.fullmatch(kid):
        raise KeysetRefused("kid must be 1 to 64 letters, digits, '-' or '_'")
    if any(key.kid == kid for key in keys):
        raise KeysetRefused("every kid must be unique")
    if not keys:
        return [KeyEntry(kid, "current", now, None, pem)]
    if any(key.state == "next" for key in check(keys)):
        raise KeysetRefused("a next key exists already: promote it first")
    return [*keys, KeyEntry(kid, "next", now, None, pem)]


def promote(keys: list[KeyEntry], now: datetime) -> list[KeyEntry]:
    upcoming = [key for key in check(keys) if key.state == "next"]
    if not upcoming:
        raise KeysetRefused("there is no next key: add one first")
    if now - upcoming[0].published_at < PUBLISH_BEFORE_SIGNING:
        raise KeysetRefused("the next key must be published for 24 hours and 5 minutes first")
    moved: list[KeyEntry] = []
    for key in keys:
        if key.state == "current":
            key = KeyEntry(key.kid, "retired", key.published_at, now, key.pem)
        elif key.state == "next":
            key = KeyEntry(key.kid, "current", key.published_at, None, key.pem)
        moved.append(key)
    return check(moved)


def drop_expired(keys: list[KeyEntry], now: datetime) -> list[KeyEntry]:
    return check(published(check(keys), now))
