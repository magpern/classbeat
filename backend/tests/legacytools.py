"""Helpers for the legacy Ghost Target tests (ADR 0029 section 3, MA1a-1). TEST ONLY: a draft as it
was stored while Ghost Target was offered, planted directly into ``class_revisions`` as the
superuser (no model, no network)."""

import copy
import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

GHOST = "ghost_target"


def with_ghost_target(content: dict[str, Any]) -> tuple[dict[str, Any], tuple[int, int]]:
    """``content`` with the first interval of its first ``main`` segment a Ghost Target game, and
    that interval's (segment, interval) position."""
    out = copy.deepcopy(content)
    for n, segment in enumerate(out["segments"]):
        if segment["kind"] == "main":
            segment["intervals"][0]["game"] = GHOST
            return out, (n, 0)
    raise AssertionError("no main segment")


def content_hash(content: dict[str, Any]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def legacy_child(
    sql: Callable[..., Any], class_id: UUID, base: UUID, number: int = 90
) -> tuple[UUID, dict[str, Any], tuple[int, int]]:
    """A copy of draft ``base`` (its music and provenance) whose content has a Ghost Target game,
    made the class's current revision: (its id, its content, the game's position)."""
    row = sql("SELECT content FROM class_revisions WHERE id = :id", id=base).one()
    content, at = with_ghost_target(row.content)
    child = uuid.uuid4()
    sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, parent_id) SELECT :id, owner_id, class_id, :n, kind, "
        "CAST(:content AS jsonb), :h, music, provenance, id FROM class_revisions WHERE id = :base",
        id=child, n=number, content=json.dumps(content), h=content_hash(content), base=base,
    )  # fmt: skip
    sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=child, c=class_id)
    return child, content, at


def has_ghost(value: Any) -> bool:
    return GHOST in json.dumps(value)
