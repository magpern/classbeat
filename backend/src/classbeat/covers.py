"""Album and playlist cover URLs (ADR 0013 section 9 and its amendment of 2026-11-03; threat
model T73 and T97).

The one URL from a Spotify answer that classbeat keeps: the album image ``mowl-workouts`` puts
in a track entry's ``image``. It is kept only when the **whole** string is
``https://i.scdn.co/image/`` and 40 lowercase hex digits (``re.fullmatch``, so a trailing newline,
a query, another host or scheme, uppercase hex or another length is no match), and it is checked
both before it is stored in a ``song_list`` and before a page is given it. Anything else is
``None``.

The server never requests it, and it never goes into a model's input or comes from a model's
output: only the backend's own records carry it (``SearchRegistry`` entries, pool picks and
tracks, kept and chosen songs), and none of their model views shows it. The browser loads it
through ``CoverArt`` alone, without a referrer, under the ``img-src`` of the app's CSP
(``classbeat.headers``).
"""

import re
from typing import Final

COVER_HOST: Final = "https://i.scdn.co"
COVER_URL: Final = re.compile(r"https://i\.scdn\.co/image/[0-9a-f]{40}")


def cover_url(value: object) -> str | None:
    """``value`` when it is an album cover URL on the pinned host, else ``None``."""
    if type(value) is not str or COVER_URL.fullmatch(value) is None:
        return None
    return value


# Playlist covers (ADR 0013 section 9, amendment of 2026-11-03, #537): the image of each of the
# user's Spotify playlists, for the New class picker only. Exactly three whole-string shapes: the
# album pin (a playlist with fewer than four albums, older uploads), Spotify's 2x2 mosaic of four
# album images at 300 or 640 px, and uploaded covers on the two image-cdn hosts. Anything else is
# no cover. Never stored, never in a ``song_list``: a song keeps ``cover_url`` alone.
_HEX40: Final = "[0-9a-f]{40}"
PLAYLIST_COVER_URL: Final = re.compile(
    rf"https://(?:i\.scdn\.co/image/{_HEX40}"
    rf"|mosaic\.scdn\.co/(?:300|640)/(?:{_HEX40}){{4}}"
    rf"|image-cdn-(?:ak|fa)\.spotifycdn\.com/image/{_HEX40})"
)
# The CSP's ``img-src`` sources (``classbeat.headers``), path-scoped to the shapes above: a source
# ending in ``/`` matches that prefix. The browser ignores the path after a redirect (the host is
# still checked), so the whole-string checks here and in ``CoverArt`` stay the main control.
IMG_SOURCES: Final = (
    "https://i.scdn.co/image/",
    "https://mosaic.scdn.co/300/",
    "https://mosaic.scdn.co/640/",
    "https://image-cdn-ak.spotifycdn.com/image/",
    "https://image-cdn-fa.spotifycdn.com/image/",
)


def playlist_cover_url(value: object) -> str | None:
    """``value`` when it is a playlist cover URL of one of the three pinned shapes, else None."""
    if type(value) is not str or PLAYLIST_COVER_URL.fullmatch(value) is None:
        return None
    return value
