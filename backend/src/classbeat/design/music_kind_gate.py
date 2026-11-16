"""The soundless gate's process setting (ADR 0007 amendment of 2026-11-03, item 2; #527, #824):
``CLASSBEAT_SOUNDLESS_ENABLED`` and whether the web's own ``fake`` provider counts as covering
soundless. Moved out of ``classbeat.design.music_kind`` unchanged (SR C5b, the repository's
split-on-touch rule); ``music_kind`` re-exports every name.
"""

from dataclasses import dataclass

from classbeat.config import Settings


@dataclass(frozen=True, slots=True)
class SoundlessGate:
    enabled: bool = False  # CLASSBEAT_SOUNDLESS_ENABLED
    # The web's own ``fake`` provider outside prod (see the module); never true in prod.
    fake_covers: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "SoundlessGate":
        return cls(
            enabled=settings.soundless_enabled,
            fake_covers=fake_covers(settings),
        )


def fake_covers(settings: Settings) -> bool:
    """Whether this process's ``fake`` provider counts as covering soundless: only when its own
    ``CLASSBEAT_MODEL_PROVIDER`` is ``fake`` and ``CLASSBEAT_ENVIRONMENT`` is not ``prod``."""
    return settings.model_provider == "fake" and settings.environment != "prod"


_gate = SoundlessGate()


def configure(gate: SoundlessGate | None) -> None:
    """Set the process's gate (None: soundless off)."""
    global _gate
    _gate = gate or SoundlessGate()


def current() -> SoundlessGate:
    return _gate
