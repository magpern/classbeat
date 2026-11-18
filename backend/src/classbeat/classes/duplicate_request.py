"""The request of a duplicate (split out of ``classbeat.classes.duplicate``, the repository's
split-on-touch rule): the cleaned title (``classbeat.class_title_policy``, #1233) and the digest
an ``Idempotency-Key`` stands for."""

import hashlib
from uuid import UUID

from classbeat.class_title_policy import clean_title as clean_title
from classbeat.jobs.http import canonical_json


def request_digest(class_id: UUID, revision_id: UUID, content_hash: bytes, title: str) -> str:
    """The SHA-256 (hex) of the canonical request a key stands for."""
    request = {
        "class_id": str(class_id),
        "revision_id": str(revision_id),
        "content_hash": content_hash.hex(),
        "title": title,
    }
    return hashlib.sha256(canonical_json(request)).hexdigest()
