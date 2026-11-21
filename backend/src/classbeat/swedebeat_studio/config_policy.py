"""``CLASSBEAT_SWEDEBEAT_STUDIO`` (ADR 0036 sections 2 and 10): which parts of the SwedeBeat studio
this environment runs. Pure: no I/O.

- A comma-separated list, empty by default (the studio off: SwedeBeat classes keep today's match
  and the manual hand-off). Spaces around a value are ignored.
- The accepted values are ``design`` (the studio itself), the three outputs ``swedebeat_playlist``,
  ``swedebeat_mix`` and ``mowl``, and ``bass_swap`` (ADR 0036 amendment B8: the mix's bass swap,
  #1330, off by default; **not an output**). Anything else, an empty item or a value given twice
  refuses to start the app (``ValueError`` from ``Settings``).
- An output value needs ``design``; ``mowl`` and ``bass_swap`` need ``swedebeat_mix``.

Each value's live gates are the owner's (ADR 0036 section 10); this module only reads the list.
"""

from typing import Final

DESIGN: Final = "design"
BASS_SWAP: Final = "bass_swap"
VALUES: Final = (DESIGN, "swedebeat_playlist", "swedebeat_mix", "mowl", BASS_SWAP)
# Explicit, never derived from ``VALUES``: a value that is not an output (``bass_swap``) must
# never become one (``output_choice_policy.STUDIO_VALUES`` lists the same three).
OUTPUTS: Final = frozenset({"swedebeat_playlist", "swedebeat_mix", "mowl"})


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
    if BASS_SWAP in values and "swedebeat_mix" not in values:
        raise ValueError("swedebeat_studio: bass_swap needs swedebeat_mix")
    return values


def canonical(raw: str) -> str:
    """``raw`` checked and written in ``VALUES`` order (the stored setting)."""
    values = studio_values(raw)
    return ",".join(value for value in VALUES if value in values)
