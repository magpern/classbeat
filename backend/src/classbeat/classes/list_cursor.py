"""The class list's page cursor (``GET /api/classes``): ``(updated_at, id)`` of the last class of
the page, as unpadded URL-safe base64 of a JSON pair. Split out of ``classbeat.classes.routes``."""

import base64
import json
from datetime import datetime
from uuid import UUID

from classbeat.classes.service import Cursor


def encode_cursor(after: Cursor) -> str:
    raw = json.dumps([after[0].isoformat(), str(after[1])]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> Cursor | None:
    try:
        at, class_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        when = datetime.fromisoformat(at)
        parsed = UUID(class_id)
    except (ValueError, TypeError):  # bad base64, JSON, shape, timestamp or uuid
        return None
    # A cursor names an instant: a naive timestamp would be read in the session's time zone.
    if when.utcoffset() is None:
        return None
    return when, parsed
