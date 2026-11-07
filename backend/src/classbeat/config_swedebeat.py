"""Parsing the ``swedebeat_app`` secret (ADR 0015 section 4) for ``classbeat.config``.

Pure (no I/O): the caller reads the secret file. Re-exported from ``classbeat.config``, so callers
keep importing it from there.
"""

import json

from pydantic import SecretStr


def swedebeat_app_keys(value: SecretStr) -> dict[str, str] | None:
    """The ``swedebeat_app`` file's key and iv if it is a JSON object whose ``key`` is 32 and
    ``iv`` 16 single-byte characters (the library encodes both as latin-1); else ``None``."""
    try:
        data = json.loads(value.get_secret_value())
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != {"key", "iv"}:  # pyright: ignore[reportUnknownArgumentType]
        return None
    key, iv = data["key"], data["iv"]
    if not isinstance(key, str) or not isinstance(iv, str):
        return None
    try:
        sized = len(key.encode("latin-1")) == 32 and len(iv.encode("latin-1")) == 16
    except UnicodeEncodeError:
        return None
    return {"key": key, "iv": iv} if sized else None
