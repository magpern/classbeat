"""The request of a duplicate (split out of ``classbeat.classes.duplicate``, the repository's
split-on-touch rule): the cleaned title and the digest an ``Idempotency-Key`` stands for."""

import hashlib
import unicodedata
from typing import Final
from uuid import UUID

from classbeat.jobs.http import canonical_json

TITLE_MAX: Final = 100  # the classes_title check of migration 0004


def clean_title(raw: str) -> str | None:
    """The title with line breaks, tabs and other blanks turned into spaces, control and format
    characters removed and blanks collapsed, 1 to 100 characters; ``None`` otherwise."""
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in raw
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    title = " ".join(kept.split())
    return title if 1 <= len(title) <= TITLE_MAX else None


def request_digest(class_id: UUID, revision_id: UUID, content_hash: bytes, title: str) -> str:
    """The SHA-256 (hex) of the canonical request a key stands for."""
    request = {
        "class_id": str(class_id),
        "revision_id": str(revision_id),
        "content_hash": content_hash.hex(),
        "title": title,
    }
    return hashlib.sha256(canonical_json(request)).hexdigest()
