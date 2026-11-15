"""``CLASSBEAT_SWEDEBEAT_STUDIO`` (ADR 0036 sections 2 and 10): which parts of the SwedeBeat studio
this environment runs. Pure: no I/O.

- A comma-separated list, empty by default (the studio off: SwedeBeat classes keep today's match
  and the manual hand-off). Spaces around a value are ignored.
- The accepted values are ``design`` (the studio itself), ``swedebeat_playlist``,
  ``swedebeat_mix`` and ``mowl``. Anything else, an empty item or a value given twice refuses to
  start the app (``ValueError`` from ``Settings``).
- An output value needs ``design``; ``mowl`` needs ``swedebeat_mix``.

Each value's live gates are the owner's (ADR 0036 section 10); this module only reads the list.
"""

from typing import Final

DESIGN: Final = "design"
VALUES: Final = (DESIGN, "swedebeat_playlist", "swedebeat_mix", "mowl")
OUTPUTS: Final = frozenset(VALUES[1:])


def studio_values(raw: str) -> frozenset[str]:
    """The set ``raw`` names, or ``ValueError`` (the message names the rule, never the input)."""
    if not raw.strip():
        return frozenset()
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        raise ValueError("swedebeat_studio has an empty value")
    if any(part not in VALUES for part in parts):
        raise ValueError(f"swedebeat_studio accepts only {', '.join(VALUES)}")
    if len(set(parts)) != len(parts):
        raise ValueError("swedebeat_studio names each value once")
    values = frozenset(parts)
    if values & OUTPUTS and DESIGN not in values:
        raise ValueError("swedebeat_studio: an output needs design")
    if "mowl" in values and "swedebeat_mix" not in values:
        raise ValueError("swedebeat_studio: mowl needs swedebeat_mix")
    return values


def canonical(raw: str) -> str:
    """``raw`` checked and written in ``VALUES`` order (the stored setting)."""
    values = studio_values(raw)
    return ",".join(value for value in VALUES if value in values)
