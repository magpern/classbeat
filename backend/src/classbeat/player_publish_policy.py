"""The rules of one publication to classbeat-player (ADR 0034 section 5). Pure: no I/O.

- ``operation_key`` and ``publication_hash``: the external operation's idempotency key
  ``player:<source_class_id>:<number>`` and its request hash, the SHA-256 of the canonical JSON
  of ``{kind, source_class_id, publication_number, removed, document_hash}`` (``document_hash``
  null for a removal). Neither names the connection, the job or the user.
- ``envelope``: the body of ``POST /api/source/publications``. A removal carries no document and
  no hash (the player's publication schema).
- ``answer``: what the player's answer to a publication means for the operation (the table of
  the ADR's "Outcomes").
- ``reconciled``: what ``GET /api/source/classes/{id}`` proves about an uncertain publication.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

SEND_KIND: Final = "player_send"
REMOVE_KIND: Final = "player_remove"
PUBLICATIONS_PATH: Final = "/api/source/publications"
CLASSES_PATH: Final = "/api/source/classes/"
# The player's bound on a number we take (ADR 0034 section 5: ``N < 2^62``); the table's check.
NUMBER_LIMIT: Final = 2**62

Answer = Literal["applied", "ahead", "conflict", "unauthorized", "refused", "internal", "unsure"]
Reconciled = Literal["succeeded", "failed", "inconclusive"]


def valid_number(number: object) -> bool:
    return isinstance(number, int) and not isinstance(number, bool) and 0 < number < NUMBER_LIMIT


def operation_key(source_class_id: UUID, number: int) -> str:
    return f"player:{source_class_id}:{number}"


def publication_hash(
    kind: str, source_class_id: UUID, number: int, document_hash: str | None
) -> bytes:
    body = {
        "kind": kind,
        "source_class_id": str(source_class_id),
        "publication_number": number,
        "removed": kind == REMOVE_KIND,
        "document_hash": document_hash,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).digest()


def envelope(
    source_class_id: UUID,
    number: int,
    document: Mapping[str, Any] | None,
    document_hash: str | None,
) -> dict[str, Any]:
    """A send with ``document`` and its hash, or a removal with neither."""
    body: dict[str, Any] = {
        "source_class_id": str(source_class_id),
        "publication_number": number,
        "removed": document is None,
    }
    if document is not None:
        body["content_hash"] = document_hash
        body["document"] = dict(document)
    return body


@dataclass(frozen=True, slots=True)
class Judged:
    """``answer``, and for ``ahead`` the player's number the counter moves to."""

    answer: Answer
    ahead_to: int | None = None


def _number_in(body: Mapping[str, Any] | None) -> object:
    return None if body is None else body.get("highest_publication_number")


def answer(status: int, body: Mapping[str, Any] | None, number: int, last: int) -> Judged:
    """The player's answer to publication ``number`` (``last``: our counter before it).

    ``applied`` (200 applied or repeat), ``ahead`` (409 stale with ``last < N < 2^62``),
    ``internal`` (409 stale with any other N), ``conflict``, ``unauthorized`` (401),
    ``refused`` (another 4xx) or ``unsure`` (a 5xx or an answer that is not one of these)."""
    error = None if body is None else body.get("error")
    if status == 200:
        known = body is not None and body.get("status") in ("applied", "repeat")
        return Judged("applied" if known else "unsure")
    if status == 401:
        return Judged("unauthorized")
    if status == 409 and error == "stale_publication":
        ahead = _number_in(body)
        if valid_number(ahead) and isinstance(ahead, int) and last < ahead:
            return Judged("ahead", ahead)
        return Judged("internal")
    if status == 409 and error == "publication_conflict":
        return Judged("conflict")
    if 400 <= status < 500 and status != 408:
        return Judged("refused")
    return Judged("unsure")


def reconciled(
    status: int, body: Mapping[str, Any] | None, source_class_id: UUID, number: int, removed: bool
) -> Reconciled:
    """What the player's numbers prove about our publication ``number``: the same number of the
    same kind is ``succeeded``, a lower one or none ``failed``; anything else is
    ``inconclusive`` (the row stays ``uncertain`` for the user)."""
    if status == 404:
        return "failed" if body is not None and body.get("error") == "not_found" else "inconclusive"
    if status != 200 or body is None or body.get("source_class_id") != str(source_class_id):
        return "inconclusive"
    highest = _number_in(body)
    if not valid_number(highest) or not isinstance(highest, int):
        return "inconclusive"
    if highest < number:
        return "failed"
    if highest == number and body.get("removed") is removed:
        return "succeeded"
    return "inconclusive"
