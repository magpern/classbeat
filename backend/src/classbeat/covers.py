"""Album cover URLs (ADR 0013 section 9; threat model T73 and T97).

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
