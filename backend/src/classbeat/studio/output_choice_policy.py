"""The user's output choice for a studio draft (ADR 0036 sections 6 and 10, SR C6a). Pure: no I/O.

The outputs are ``spotify_playlist`` (the final Spotify playlist), ``swedebeat_playlist``,
``swedebeat_mix`` and ``mowl``, always kept in that order (``canonical``), each at most once.

``refusal`` is the one check of a choice, against the outputs this environment has on
(``enabled``) and whether the draft's mix spec repeats a song (``repeats``,
``mix_repeat_calc.has_repeats`` of its references), with one named reason, in this order:

- ``invalid_input``: no output, an unknown one or one named twice;
- ``mowl_needs_mix``: ``mowl`` without ``swedebeat_mix`` (the MOWL workout attaches the mix). The
  SwedeBeat playlist needs no render and no MOWL, and the mix does not need the playlist;
- ``playlist_has_repeats``: ``swedebeat_playlist`` for a draft whose spec plays a song in more than
  one piece (ADR 0036 amendment A8: such a class is mix-only until the owner's live check A9 shows
  the gateway and the app accept one track twice with distinct cuts);
- ``output_off``: an output this environment does not run (``enabled_outputs``).

``enabled_outputs`` is ADR 0036 section 10: ``swedebeat_playlist``, ``swedebeat_mix`` and ``mowl``
follow their values of ``CLASSBEAT_SWEDEBEAT_STUDIO`` (``config_policy`` already refuses ``mowl``
without ``swedebeat_mix`` at start), and the final Spotify playlist follows Spotify's own live
activation (song picking), as every Spotify playlist does. Every other value of the setting
(``design``, ``bass_swap``: ADR 0036 amendment B8) is never an output.
"""

from collections.abc import Collection, Iterable
from typing import Final, Literal

Output = Literal["spotify_playlist", "swedebeat_playlist", "swedebeat_mix", "mowl"]
OUTPUTS: Final[tuple[Output, ...]] = (
    "spotify_playlist",
    "swedebeat_playlist",
    "swedebeat_mix",
    "mowl",
)
STUDIO_VALUES: Final = frozenset({"swedebeat_playlist", "swedebeat_mix", "mowl"})
# The outputs that need the one render of the mix (section 6): only the mix itself. MOWL attaches
# that same render; the playlists need none.
RENDERED: Final = frozenset({"swedebeat_mix"})


def canonical(chosen: Iterable[str]) -> tuple[str, ...]:
    """``chosen`` in ``OUTPUTS`` order; ``ValueError`` for an unknown or a repeated output."""
    given = list(chosen)
    if len(set(given)) != len(given):
        raise ValueError("an output is chosen once")
    if any(o not in OUTPUTS for o in given):
        raise ValueError("an unknown output")
    return tuple(o for o in OUTPUTS if o in given)


def enabled_outputs(studio_values: Collection[str], *, spotify_on: bool) -> frozenset[str]:
    """The outputs this environment runs (see the module docstring)."""
    on = {v for v in studio_values if v in STUDIO_VALUES}
    if spotify_on:
        on.add("spotify_playlist")
    return frozenset(on)


def refusal(chosen: Collection[str], enabled: Collection[str], *, repeats: bool) -> str | None:
    """The reason ``chosen`` is refused, or None (see the module docstring)."""
    try:
        outputs = canonical(chosen)
    except ValueError:
        return "invalid_input"
    if not outputs:
        return "invalid_input"
    if "mowl" in outputs and "swedebeat_mix" not in outputs:
        return "mowl_needs_mix"
    if repeats and "swedebeat_playlist" in outputs:
        return "playlist_has_repeats"
    if any(o not in enabled for o in outputs):
        return "output_off"
    return None


def needs_render(chosen: Collection[str]) -> bool:
    """One render of the mix is needed (only for the mix; never for a playlist alone)."""
    return any(o in RENDERED for o in chosen)
