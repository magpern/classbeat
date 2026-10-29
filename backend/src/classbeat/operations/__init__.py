"""External operations (ADR 0005 section 7): every write to a user's provider account, made at
most once and never retried automatically when it may have taken effect.

- ``classbeat.operations.protocol``: ``perform``, the four steps for one write by a job, and
  ``classbeat.operations.requests``: what the job asks it to write (``OperationRequest``);
- ``classbeat.operations.kinds``: the per-kind seam (``Reconciler``, ``on_succeeded``);
- ``classbeat.operations.attach``: ``mowl_attach_spotify_playlist`` and its playlist link;
- ``classbeat.operations.user`` and ``routes``: **Check again** and **Do it anyway**;
- ``classbeat.operations.orphans``: orphaned ``pending`` class writes and the unresolved-write
  rule (ADR 0007 section 8).

A job kind's own package; it writes through the job's fence (``JobRun.fenced``), except the
user actions of ``classbeat.operations.user`` and ``orphans.resolve_now`` (web routes).
"""

from classbeat.operations.attach import ATTACH, attach_spotify_playlist, linked_playlist
from classbeat.operations.kinds import (
    INCONCLUSIVE,
    OPERATION_KINDS,
    Operation,
    OperationKind,
    OperationKinds,
    ReconcileInput,
    Reconciliation,
    request_hash,
)
from classbeat.operations.protocol import CallResult, Done, UncertainAnswer, perform
from classbeat.operations.requests import OperationRequest

__all__ = [
    "ATTACH",
    "INCONCLUSIVE",
    "OPERATION_KINDS",
    "CallResult",
    "Done",
    "Operation",
    "OperationKind",
    "OperationKinds",
    "OperationRequest",
    "ReconcileInput",
    "Reconciliation",
    "UncertainAnswer",
    "attach_spotify_playlist",
    "linked_playlist",
    "perform",
    "request_hash",
]
