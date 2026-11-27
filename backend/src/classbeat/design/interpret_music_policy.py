"""The music of an interpreted request (ADR 0029 section 4, rule 4; pure: no database, no network,
no model).

``choose`` follows ``request_policy.music_order``. A cycling class takes the user's own choice
(``music_override``, which includes "No music"), then the name the instructor wrote (the
interpreter's index into ``options``, range-checked here too), then a song pick when Spotify is
ready; there is no default music (roadmap Amendment 25). A playlist, named or chosen, is Spotify's:
without a Spotify connection (``spotify``) it is not taken, and a class with no music left is Not
as asked ``needs_spotify``, or ``song_picking_unavailable`` when song picking is off for the user
(nothing to connect: the Spotify page is hidden). A Swedebeat mix and "No music" need no Spotify. A
running class uses MOWL playlists only (ADR 0018), the first one by default; with none it has no
music and is Not as asked ``needs_mowl_playlist``. One ``music`` item records the source chosen (a
closed value), after a Not as asked item for a choice a running class could not take.

The music is the design input's own shape (``{"source": ...}``; ``{"source": "pick"}`` for a song
pick), so the hand-off enqueues it unchanged. An id never reaches a model: the interpreter sees the
names only (``interpret_input.music_list``).
"""

from collections.abc import Iterable
from typing import Final, Literal, NamedTuple

from classbeat.agent.interpretation_record import InterpretationItem, Origin, keep_quote
from classbeat.agent.interpreted import StatedMusic
from classbeat.design import request_policy

NEEDS_MOWL_PLAYLIST: Final = request_policy.NEEDS_MOWL_PLAYLIST
NEEDS_SPOTIFY: Final = request_policy.NEEDS_SPOTIFY
SONG_PICKING_UNAVAILABLE: Final = request_policy.SONG_PICKING_UNAVAILABLE

Music = dict[str, str]


class MusicOption(NamedTuple):
    """One of the instructor's MOWL playlists or Swedebeat mixes: the name the interpreter is
    shown and the id a design job takes (never shown to a model)."""

    kind: Literal["playlist", "mix"]
    name: str
    ref: str


def without_spotify_is_refused(
    *,
    music_source: str | None,
    sport_override: str | None,
    mixes: bool,
    composer: bool = False,
) -> bool:
    """Whether an interpretation with no Spotify connection can only end Not as asked
    ``needs_spotify``, so ``admit`` refuses it before any model call. It cannot when the music is
    "No music" or a Swedebeat mix, when the sport is (or may be read as: no sport was chosen, and
    running is always offered, #1544) running, which has MOWL playlists only (ADR 0018), or when
    the request may name a Swedebeat mix (``mixes``). Never
    with the composer available (``composer``): its sketch needs no Spotify, and the song search
    asks for the connection when it is needed (ADR 0038 amendment of 2026-11-24, decision 6)."""
    if composer:
        return False
    if music_source in ("none", "mowl_swedebeat_mix") or sport_override != "cycling":
        return False
    return not (music_source is None and mixes)


def no_music_reason(picking: bool) -> str:
    """Why a cycling class has no music: the Spotify connection is missing (``needs_spotify``, when
    song picking admits the user, so the connection can be made) or song picking is not available
    to the user at all (``song_picking_unavailable``, so there is nothing to connect)."""
    return NEEDS_SPOTIFY if picking else SONG_PICKING_UNAVAILABLE


def stored_no_music_reason(items: Iterable[InterpretationItem]) -> str:
    """The reason a stored cycling interpretation has no music: its music item's closed code."""
    for item in items:
        if item.path == "music" and item.reason_code in (NEEDS_SPOTIFY, SONG_PICKING_UNAVAILABLE):
            return item.reason_code
    return NEEDS_SPOTIFY


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
    spotify: bool,
    override: Music | None,
    out: list[InterpretationItem],
    *,
    picking: bool = True,
) -> Music | None:
    """The music, or None (nothing to take: ``needs_spotify`` or, with ``picking`` off,
    ``song_picking_unavailable`` for cycling; ``needs_mowl_playlist`` for running); the items go to
    ``out``."""
    running = sport == request_policy.ALL_SPORTS[1]
    available: dict[str, Music] = {}
    if override is not None:
        source = override.get("source")
        if running and source != "mowl_spotify_playlist":
            out.append(_item("not_as_asked", source, NEEDS_MOWL_PLAYLIST))
        elif running or source != "mowl_spotify_playlist" or spotify:
            available["override"] = override
    if named is not None and named.value < len(options):
        option = options[named.value]
        if running and option.kind != "playlist":
            if named.origin == "explicit" and "override" not in available:
                out.append(_item("not_as_asked", "mix", NEEDS_MOWL_PLAYLIST))
        elif running or option.kind != "playlist" or spotify:
            available["name_match"] = _choice(option)
    if pick_ready:
        available["pick"] = {"source": "pick"}
    if running:
        first = next((o for o in options if o.kind == "playlist"), None)
        if first is not None:
            available["first_playlist"] = _choice(first)
    chosen = request_policy.choose_music(sport, available)
    if chosen is None:
        out.append(
            _item(
                "not_as_asked", None, NEEDS_MOWL_PLAYLIST if running else no_music_reason(picking)
            )
        )
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
