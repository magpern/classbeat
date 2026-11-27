"""The ``composer_flow`` gate (ADR 0038 section 2, #1185; the ``running_enabled`` pattern of ADR
0018 section 5).

``CLASSBEAT_COMPOSER_FLOW`` is the environment's switch (``ComposerGate.enabled``, set by the web
app at start). The flow is on only when that switch is on **and** the stored ``composer_flow``
is exactly ``on`` (``flags.core.composer_switch``: missing, ``null`` or invalid is off), read on
every request: ``composer_on``, what ``GET /api/connections`` returns and ``interpret_request``'s
admit reads. Admit records the answer as the job's server field ``composer``, so the worker never
reads the flag: a job admitted with it off behaves exactly as before, whatever the flag says by
the time it runs.

Without a configured gate the switch is off.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.flags import effective


@dataclass(frozen=True, slots=True)
class ComposerGate:
    enabled: bool = False  # CLASSBEAT_COMPOSER_FLOW

    @classmethod
    def from_settings(cls, settings: Settings) -> "ComposerGate":
        return cls(enabled=settings.composer_flow)


_gate = ComposerGate()


def configure(gate: ComposerGate | None) -> None:
    """Set the process's gate (None: the composer flow off)."""
    global _gate
    _gate = gate or ComposerGate()


def current() -> ComposerGate:
    return _gate


async def composer_on(db: AsyncSession) -> bool:
    """The effective ``composer_flow``: the environment's switch and a stored ``on``."""
    return await effective.composer_flow(db, current().enabled)


async def composer_available(db: AsyncSession, ctx: UserContext) -> bool:
    """The composer for **this user** (ADR 0038 amendment of 2026-11-24, #1484): the operator's
    switch pair (``composer_on``, unchanged) and the user's plan covering the sketch
    (``sketch_gate.admit_sketch`` admits). ``interpret_request``'s admit, ``GET /api/connections``
    and the hand-off read it."""
    from classbeat.design import sketch_gate  # lazy: the web's routes import this module early

    if not await composer_on(db):
        return False
    return await sketch_gate.admit_sketch(db, ctx) is None
