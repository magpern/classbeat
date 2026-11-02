"""The Swedebeat app step (ADR 0016 section 7, B2): the hand-off row and its verify job.

- ``classbeat.swedebeat_handoff.kind``: ``swedebeat_verify_mix``, registered on import;
- ``classbeat.swedebeat_handoff.verdict``: the length band and the positions a short mix names;
- ``classbeat.swedebeat_handoff.rows``: the owned row, in the caller's session;
- ``classbeat.swedebeat_handoff.adopt``: ``design_draft`` taking a verified mix as the class;
- ``classbeat.swedebeat_handoff.config``: the switch and the worker's key ring.

The web transactions live in ``classbeat.classes.handoff`` (a user's own request). This package
writes a job's result only through the fence.
"""

from classbeat.swedebeat_handoff.config import HandoffConfig, configure
from classbeat.swedebeat_handoff.kind import KIND, VERIFY_MIX, VerifyInput

__all__ = ["KIND", "VERIFY_MIX", "HandoffConfig", "VerifyInput", "configure"]
