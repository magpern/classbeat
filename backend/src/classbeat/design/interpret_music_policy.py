"""The music of an interpreted request (ADR 0029 section 4, rule 4; pure: no database, no network,
no model).

``choose`` follows ``request_policy.music_order``: the user's own choice (``music_override``), then
the name the instructor wrote (the interpreter's index into ``options``, range-checked here too),
then a song pick when Spotify is ready, then the first MOWL playlist, then no music (cycling only).
A running class uses MOWL playlists only (ADR 0018); with none it has no music and is Not as asked
``needs_mowl_playlist``. One ``music`` item records the source chosen (a closed value), after a Not
as asked item for a choice a running class could not take.

The music is the design input's own shape (``{"source": ...}``; ``{"source": "pick"}`` for a song
pick), so the hand-off enqueues it unchanged. An id never reaches a model: the interpreter sees the
names only (``interpret_input.music_list``).
"""

from typing import Final, Literal, NamedTuple

from classbeat.agent.interpretation_record import InterpretationItem, Origin, keep_quote
from classbeat.agent.interpreted import StatedMusic
from classbeat.design import request_policy

NEEDS_MOWL_PLAYLIST: Final = request_policy.NEEDS_MOWL_PLAYLIST

Music = dict[str, str]


class MusicOption(NamedTuple):
    """One of the instructor's MOWL playlists or Swedebeat mixes: the name the interpreter is
    shown and the id a design job takes (never shown to a model)."""

    kind: Literal["playlist", "mix"]
    name: str
    ref: str


def _item(
    origin: Origin, value: str | None, reason: str | None = None, quote: str | None = None
) -> InterpretationItem:
    return InterpretationItem(
        path="music", origin=origin, reason_code=reason, value=value, quote=quote
    )


def choose(
    named: StatedMusic | None,
    request: str,
    sport: str,
    options: tuple[MusicOption, ...],
    pick_ready: bool,
    override: Music | None,
    out: list[InterpretationItem],
) -> Music | None:
    """The music, or None (a running class with no MOWL playlist); the items go to ``out``."""
    running = sport == request_policy.ALL_SPORTS[1]
    available: dict[str, Music] = {}
    if override is not None:
        if running and override.get("source") != "mowl_spotify_playlist":
            out.append(_item("not_as_asked", override.get("source"), NEEDS_MOWL_PLAYLIST))
        else:
            available["override"] = override
    if named is not None and named.value < len(options):
        option = options[named.value]
        if running and option.kind != "playlist":
            if named.origin == "explicit" and "override" not in available:
                out.append(_item("not_as_asked", "mix", NEEDS_MOWL_PLAYLIST))
        else:
            available["name_match"] = _choice(option)
    if pick_ready:
        available["pick"] = {"source": "pick"}
    first = next((o for o in options if o.kind == "playlist"), None)
    if first is not None:
        available["first_playlist"] = _choice(first)
    available["soundless"] = {"source": "none"}
    chosen = request_policy.choose_music(sport, available)
    if chosen is None:
        out.append(_item("not_as_asked", None, NEEDS_MOWL_PLAYLIST))
        return None
    origin: Origin = "default"
    quote = None
    if chosen == "override":
        origin = "explicit"
    elif chosen == "name_match" and named is not None:
        origin, quote = named.origin, keep_quote(named.quote, request)
    out.append(_item(origin, chosen, None, quote))
    return available[chosen]


def _choice(option: MusicOption) -> Music:
    if option.kind == "mix":
        return {"source": "mowl_swedebeat_mix", "mix_id": option.ref}
    return {"source": "mowl_spotify_playlist", "spotify_playlist_id": option.ref}
