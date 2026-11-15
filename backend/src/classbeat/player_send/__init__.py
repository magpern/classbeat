"""Send to player and the removal on class deletion (ADR 0034 section 5, issue PC-5).

- ``classbeat.player_send.send_kind``: the ``send_to_player`` job kind (the user's press for one
  revision), registered in ``classbeat.jobs.KINDS`` on import;
- ``classbeat.player_send.remove_kind``: the ``player_remove`` job kind, enqueued only by a class's
  deletion (``publications.on_class_delete``), never through the jobs API;
- ``classbeat.player_send.operation``: the ``player_send`` and ``player_remove`` external
  operations (their reconciler and ``on_succeeded``), registered in
  ``classbeat.operations.OPERATION_KINDS`` on import;
- ``classbeat.player_send.publish``: one publication through the protocol, and the outcome
  mapping of the player's answers;
- ``classbeat.player_send.publications``: the ``player_publications`` rows;
- ``classbeat.player_send.document``: the class document of a revision, from the database;
- ``classbeat.player_send.source_calls``: the two HTTP calls to the player's source API;
- ``classbeat.player_send.runtime``: the worker's key ring and player client factory.

The pure rules are ``classbeat.player_publish_policy`` and ``classbeat.player_class_calc``.
Imported by the worker (which configures the runtime) and by the web app (the jobs API admits a
send; Check again finds the kinds). Nothing a model returns names these kinds: no agent tool
creates a job (``tests/test_player_send_rules.py``).
"""

from classbeat.player_send.operation import REMOVE, SEND
from classbeat.player_send.remove_kind import PLAYER_REMOVE, PlayerRemoveInput
from classbeat.player_send.runtime import Runtime, configure, current_runtime
from classbeat.player_send.send_kind import KIND, SEND_TO_PLAYER, SendToPlayerInput

__all__ = [
    "KIND",
    "PLAYER_REMOVE",
    "REMOVE",
    "SEND",
    "SEND_TO_PLAYER",
    "PlayerRemoveInput",
    "Runtime",
    "SendToPlayerInput",
    "configure",
    "current_runtime",
]
