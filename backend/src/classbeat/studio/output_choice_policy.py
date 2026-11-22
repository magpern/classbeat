"""The user's output choice for a studio draft (ADR 0036 sections 6 and 10, SR C6a). Pure: no I/O.

The outputs are ``spotify_playlist`` (the final Spotify playlist), ``swedebeat_playlist``,
``swedebeat_mix`` and ``mowl``, always kept in that order (``canonical``), each at most once.

``refusal`` is the one check of a choice, against the outputs this environment has on
(``enabled``) and three facts of the draft's stored, hash-checked mix spec: whether it repeats a
song (``repeats``, ``mix_repeat_calc.has_repeats`` of its references), whether it is not whole
songs (``mixed``) and whether it is the applied form of its proposal (``applied_form``; both
``mix_applied_policy``, ADR 0036 amendment B9), and whether a cut pin is to be listened to again
(``pin_unconfirmed``, ``mix_pin_policy.listen_again``), with one named reason, in this order:

- ``invalid_input``: no output, an unknown one or one named twice;
- ``mowl_needs_mix``: ``mowl`` without ``swedebeat_mix`` (the MOWL workout attaches the mix). The
  SwedeBeat playlist needs no render and no MOWL, and the mix does not need the playlist;
- ``mix_not_chosen``: ``swedebeat_playlist`` without ``swedebeat_mix`` for a mixed draft (B9:
  nothing is mixed unless the user chose DJ mix);
- ``mix_not_applied``: a SwedeBeat output (``SWEDEBEAT``) for a draft that is not
  ``applied_form`` (B9: B5 and B6 hold for every draft approved for one, never only through the
  screen). A Spotify playlist alone is admitted from any draft (whole songs by nature, A8);
- ``pin_unconfirmed``: any output while a confirmed cut pin's music changed and it is not yet
  re-confirmed or cleared (ADR 0042 section 5, #1390: the approval as a whole is refused, by the
  server, not only the screen);
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
# The outputs written to SwedeBeat: only an applied form may bind them (B9; ``mowl`` needs the mix).
SWEDEBEAT: Final = frozenset({"swedebeat_playlist", "swedebeat_mix"})


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


def refusal(
    chosen: Collection[str],
    enabled: Collection[str],
    *,
    repeats: bool,
    mixed: bool,
    applied_form: bool,
    pin_unconfirmed: bool,
) -> str | None:
    """The reason ``chosen`` is refused, or None (see the module docstring)."""
    try:
        outputs = canonical(chosen)
    except ValueError:
        return "invalid_input"
    if not outputs:
        return "invalid_input"
    if "mowl" in outputs and "swedebeat_mix" not in outputs:
        return "mowl_needs_mix"
    if mixed and "swedebeat_playlist" in outputs and "swedebeat_mix" not in outputs:
        return "mix_not_chosen"
    if not applied_form and any(o in SWEDEBEAT for o in outputs):
        return "mix_not_applied"
    if pin_unconfirmed:
        return "pin_unconfirmed"
    if repeats and "swedebeat_playlist" in outputs:
        return "playlist_has_repeats"
    if any(o not in enabled for o in outputs):
        return "output_off"
    return None


def needs_render(chosen: Collection[str]) -> bool:
    """One render of the mix is needed (only for the mix; never for a playlist alone)."""
    return any(o in RENDERED for o in chosen)
