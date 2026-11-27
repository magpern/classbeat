"""The soundless coverage setting of this process (ADR 0007 amendments of 2026-11-03, item 2, and
2026-11-27; #527, #824, #1543): whether the web's own ``fake`` provider counts as covering
soundless. Moved out of ``classbeat.design.music_kind`` (SR C5b, the repository's split-on-touch
rule); ``music_kind`` re-exports every name.

There is no soundless switch any more (#1543, owner 2026-11-26: "No music" is always a choice).
Coverage decides only the composer's sketch (``sketch_gate``) and whether a real no-music design is
served with the stale-route warning (``music_kind.serve_model``).
"""

from dataclasses import dataclass

from classbeat.config import Settings


@dataclass(frozen=True, slots=True)
class SoundlessGate:
    # The web's own ``fake`` provider outside prod (see ``fake_covers``); never true in prod.
    fake_covers: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "SoundlessGate":
        return cls(fake_covers=fake_covers(settings))


def fake_covers(settings: Settings) -> bool:
    """Whether this process's ``fake`` provider counts as covering soundless: only when its own
    ``CLASSBEAT_MODEL_PROVIDER`` is ``fake`` and ``CLASSBEAT_ENVIRONMENT`` is not ``prod``."""
    return settings.model_provider == "fake" and settings.environment != "prod"


_gate = SoundlessGate()


def configure(gate: SoundlessGate | None) -> None:
    """Set the process's setting (None: nothing counts as covered without a recorded route)."""
    global _gate
    _gate = gate or SoundlessGate()


def current() -> SoundlessGate:
    return _gate
