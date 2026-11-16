"""Which entry of the user's MOWL mix listing is the mix classbeat published (ADR 0036 section 6,
MOWL attach; SR C7). Pure: no I/O.

- **Attach** (``attachable``): only the entry whose ``URL`` carries the **S3 object key** of the
  stored merge URL (``object_key``: the URL's path, percent-decoded, without its leading ``/``)
  **and** whose duration is within ``DURATION_TOLERANCE_SEC`` of the composed mix's. Exactly one
  such entry, or none: two entries that both match are not taken. **Never by name**: an entry with
  the mix's unique name but another URL (or none) is never attached.
- **Shown, never taken** (``named``): for a mix in doubt, the entries whose ``Name`` is exactly
  the stored unique name, for the user to confirm; nothing is attached or settled by this.

Entries are the library's ``swedebeat_mixes`` answer (``{ID, Name, URL,
DurationInMilliseconds}``); one without an id of the link table's shape, a string URL or a
duration is left out.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, cast
from urllib.parse import unquote, urlsplit

DURATION_TOLERANCE_SEC: Final = 2.0
MIX_ID: Final = re.compile(r"[0-9A-Za-z_-]{1,64}")  # the mowl_playlist_links check
MAX_URL: Final = 2048
MAX_NAME: Final = 200


@dataclass(frozen=True, slots=True)
class Listed:
    mix_id: str
    name: str
    url: str
    duration_sec: float


def object_key(url: object) -> str | None:
    """The S3 object key of an ``http(s)`` URL, or ``None``."""
    if not isinstance(url, str) or not 1 <= len(url) <= MAX_URL:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    path = unquote(parts.path).lstrip("/")
    return path or None


def entries(answer: object) -> list[Listed]:
    out: list[Listed] = []
    for raw in cast(list[Any], answer) if isinstance(answer, list) else []:
        if not isinstance(raw, dict):
            continue
        entry = cast(dict[str, Any], raw)
        mix_id, url, name = entry.get("ID"), entry.get("URL"), entry.get("Name")
        ms = entry.get("DurationInMilliseconds")
        if not isinstance(mix_id, str) or MIX_ID.fullmatch(mix_id) is None:
            continue
        if not isinstance(url, str) or len(url) > MAX_URL:
            continue
        if isinstance(ms, bool) or not isinstance(ms, int | float) or not 0 < ms < 86_400_000:
            continue
        shown = name[:MAX_NAME] if isinstance(name, str) else ""
        out.append(Listed(mix_id, shown, url, float(ms) / 1000))
    return out


def attachable(listed: Sequence[Listed], url: str, duration_sec: float) -> Listed | None:
    """The one entry to attach, or ``None`` (see the module docstring)."""
    wanted = object_key(url)
    if wanted is None:
        return None
    found = [
        e
        for e in listed
        if object_key(e.url) == wanted
        and abs(e.duration_sec - duration_sec) <= DURATION_TOLERANCE_SEC
    ]
    return found[0] if len(found) == 1 else None


def named(listed: Sequence[Listed], name: str) -> list[Listed]:
    """The entries named exactly ``name``: shown to the user, never attached."""
    return [e for e in listed if e.name == name]
