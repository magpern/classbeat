"""The song fill's idempotency key (ADR 0038 section 5, #1188), split out of
``classbeat.classes.song_fill`` so the class answer (``composer_view``) reads it too. Pure."""

import hashlib
from uuid import UUID


def fill_key(class_id: UUID, sketch_revision_id: UUID, attempt: int) -> str:
    """The fill job's idempotency key: one per class, sketch and attempt (at most 64 characters)."""
    digest = hashlib.sha256(
        class_id.bytes + sketch_revision_id.bytes + attempt.to_bytes(4, "big")
    ).hexdigest()
    return f"fill-{digest[:59]}"
