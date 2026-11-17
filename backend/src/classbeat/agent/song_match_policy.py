"""Whether a Spotify hit is the song the model named (issue #1133; pure: no network).

``find_songs`` asks Spotify for each song the model proposes from its own knowledge and keeps a
song only when a hit **is** it: the model's memory is never trusted without a Spotify match.

- **Artist**: one of the hit's artists equals the named artist, after ``normalised`` (case,
  accents, punctuation and spaces aside). "Roxette" matches a hit by "Roxette"; "Rox" does not.
- **Title**: the hit's title equals the named title after ``normalised``, once a version tail is
  set aside: a `` - ...`` suffix ("Remastered 2009", "Radio Edit") or a trailing ``(...)`` or
  ``[...]`` group, so the original song on a remaster still matches.
- **Not another version**: a hit whose title or tail says it is a live, karaoke, cover, tribute,
  instrumental, acoustic, demo or re-recorded version is refused unless the named title says so too.
- **Era**: when the search asked for years, the hit's year must be within them (a hit without a
  year is refused then).

The first hit that passes, in Spotify's order, is the match.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.agent.registry import normalised

VERSION_WORDS: Final = re.compile(
    r"\b(live|karaoke|instrumental|cover|covers|tribute|re-?recorded|acoustic|demo|8-bit|"
    r"made famous|originally performed|in the style of)\b",
    re.IGNORECASE,
)
_TAIL: Final = re.compile(r"\s+-\s+.*$|\s*[(\[][^()\[\]]*[)\]]\s*$")


def title_core(title: str) -> str:
    """The title without its version tails (`` - ...`` and trailing bracket groups)."""
    core = title.strip()
    while True:
        cut = _TAIL.sub("", core).strip()
        if cut == core or not cut:
            return core
        core = cut


def same_title(named: str, hit: str) -> bool:
    want = normalised(named)
    return bool(want) and want in (normalised(hit), normalised(title_core(hit)))


def same_artist(named: str, hit_artists: Sequence[str]) -> bool:
    want = normalised(named)
    return bool(want) and any(normalised(a) == want for a in hit_artists)


def other_version(named: str, hit: str) -> bool:
    """The hit is a live, cover or similar version the named title does not ask for."""
    return VERSION_WORDS.search(hit) is not None and VERSION_WORDS.search(named) is None


def is_match(
    artist: str,
    title: str,
    hit_title: str,
    hit_artists: Sequence[str],
    hit_year: int | None,
    years: tuple[int, int] | None,
) -> bool:
    if years is not None and (hit_year is None or not years[0] <= hit_year <= years[1]):
        return False
    return (
        same_artist(artist, hit_artists)
        and same_title(title, hit_title)
        and not other_version(title, hit_title)
    )


def _year(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value) if isinstance(value, int | str) else ""
    return int(text) if re.fullmatch(r"[12]\d{3}", text) else None


def _artists(value: Any) -> list[str]:
    items: list[Any] = list(value) if isinstance(value, list | tuple) else []  # pyright: ignore[reportUnknownArgumentType]
    return [a for a in items if isinstance(a, str)]


def matching_hits(
    artist: str,
    title: str,
    hits: Sequence[Mapping[str, Any]],
    years: tuple[int, int] | None,
) -> list[Mapping[str, Any]]:
    """The hits of the search (the library's track dicts) that are the named song, in the search's
    order: the caller keeps the first one its registry accepts (issue #1140)."""
    out: list[Mapping[str, Any]] = []
    for hit in hits:
        hit_title = hit.get("title")
        if not isinstance(hit_title, str):
            continue
        year = _year(hit.get("year"))
        if is_match(artist, title, hit_title, _artists(hit.get("artists")), year, years):
            out.append(hit)
    return out
