"""Swedebeat's catalogue search, as the library does it, without the library's network call
(ADR 0016 sections 4 and 5, B1; threat model T110).

``mowl.swedebeat.match_track`` sends the search through the process-wide urllib opener, which
never reaches ``cloud.swedebeat.se`` (it is on no urllib list). classbeat therefore builds the
same request here and sends it with the dedicated catalogue client
(``classbeat.swedebeat_match.search``):

- **The query** is the library's: ``(and (term field=songtitle '<title>') (term field=artist
  '<artist>'))`` with the title cleaned of version notes (``mowl.swedebeat.clean_title``) and each
  term encoded as the app does (a straight quote becomes U+2019, then URL-encoded with
  ``%20``), structured parser, the first five hits. **Only the title and the artist** of a song
  leave: no Spotify id, BPM, length, role, class, brief or user detail.
- **The answer** is read through a field allowlist (``parse_hits``): of each hit only ``title``,
  ``artist.title`` and ``duration`` survive, each checked; every other field, any URL and any value
  that looks like a credential is dropped inside the parser, so nothing else of the answer reaches
  the job, the revision or a log. The catalogue's ``reference`` is not kept (nothing uses it: the
  mix is built by hand in the Swedebeat app). No URL from an answer is ever fetched.
- **The verdict** is the library's: the best hit by title similarity
  (``mowl.swedebeat.title_score``) is ``found`` at or above ``SAME_SONG`` (0.85), ``unsure`` below
  it, ``missing`` with no hit. An ``unsure`` song is never counted as found.
"""

import json
import math
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Final, Literal

from mowl import swedebeat

from classbeat.egress import SWEDEBEAT_CLOUD_HOST

SEARCH_PATH: Final = "/track/extendedsearch"
SEARCH_SIZE: Final = 5
SAME_SONG: Final = swedebeat.SAME_SONG  # 0.85, the library's threshold
MAX_TEXT: Final = 200  # a kept title or artist, as a ``song_list`` keeps them
MAX_DURATION: Final = 3600  # seconds; a longer "song" is no song's length
MAX_HITS: Final = 25  # hits looked at in one answer (the request asks for five)
MAX_ANSWER_BYTES: Final = 256 * 1024
# The app's user agent (the library's): the catalogue sees the same client as for the app's own
# search of a Spotify list (ADR 0016 section 5).
USER_AGENT: Final = swedebeat.UA

Status = Literal["found", "unsure", "missing"]

# The library's ``_hits`` walks these wrappers, then ``hit`` or ``items``.
_WRAPPERS: Final = ("data", "searchListResult", "tracks", "results", "hits")
# Values that are no song's title or artist: a URL or a scheme, an e-mail or a credential's shape
# (an access key id, a JWT, a long unbroken run of key characters with digits and letters).
_URLISH = re.compile(r"(?i)([a-z][a-z0-9+.-]*://|\bwww\.|\bmailto:|@[a-z0-9-]+\.[a-z])")
_KEY_RUN = r"[0-9A-Za-z+/=_-]"
_CREDENTIALISH = re.compile(
    r"((?:AKIA|ASIA)[0-9A-Z]{12,}|eyJ[0-9A-Za-z_-]{8,}\."
    rf"|(?={_KEY_RUN}*[0-9])(?={_KEY_RUN}*[A-Za-z]){_KEY_RUN}{{24,}})"
)


def term(value: str) -> str:
    """One term value encoded as the app does (the library's ``_field``)."""
    return urllib.parse.quote(value.replace("'", "\u2019"), safe="")


def search_url(title: str, artist: str) -> str:
    """The library's search URL for one song: its cleaned title and its artist, nothing else."""
    query = (
        f"(and+(term+field=songtitle+'{term(swedebeat.clean_title(title))}')"
        f"(term+field=artist+'{term(artist)}'))"
    )
    return (
        f"https://{SWEDEBEAT_CLOUD_HOST}{SEARCH_PATH}?q={query}"
        f"&q.parser=structured&start=0&size={SEARCH_SIZE}"
    )


@dataclass(frozen=True, slots=True)
class Hit:
    """A catalogue track as the parser keeps it."""

    title: str
    artist: str
    duration: int | None  # whole seconds, or None when the answer gave no usable length


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    if not text or len(text) > MAX_TEXT:
        return None
    if _URLISH.search(text) or _CREDENTIALISH.search(text):
        return None
    return text


def _duration(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not 0 < value <= MAX_DURATION:
        return None
    return round(value)


def _hit(raw: object) -> Hit | None:
    if not isinstance(raw, dict):
        return None
    node: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    artist_node = node.get("artist")
    artist_title = artist_node.get("title") if isinstance(artist_node, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    title = _text(node.get("title"))
    artist = _text(artist_title)
    if title is None or artist is None:
        return None
    return Hit(title, artist, _duration(node.get("duration")))


def _candidates(answer: object) -> list[object]:
    """The library's ``_hits`` walk: the wrappers, then ``hit`` or ``items``, else the node."""
    node = answer
    for key in _WRAPPERS:
        if isinstance(node, dict) and key in node:
            node = node[key]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(node, dict):
        node = node.get("hit") or node.get("items") or node  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if isinstance(node, list):
        return list(node[:MAX_HITS])  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return [node] if isinstance(node, dict) and node else []


def parse_hits(body: bytes) -> list[Hit] | None:
    """The kept hits of one answer, or None when the body is not JSON (``invalid_response``).
    Only ``title``, ``artist.title`` and ``duration`` of each hit are read; a hit without a usable
    title and artist is dropped."""
    if len(body) > MAX_ANSWER_BYTES:
        return None
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return [hit for hit in map(_hit, _candidates(answer)) if hit is not None]


@dataclass(frozen=True, slots=True)
class Verdict:
    status: Status
    hit: Hit | None  # the best hit, for found and unsure


def verdict(title: str, hits: list[Hit]) -> Verdict:
    """The library's ``match_track`` verdict on the kept hits."""
    best = max(hits, key=lambda h: swedebeat.title_score(title, h.title), default=None)
    if best is None:
        return Verdict("missing", None)
    score = swedebeat.title_score(title, best.title)
    return Verdict("found" if score >= SAME_SONG else "unsure", best)


def dedupe_key(title: str, artist: str) -> tuple[str, str]:
    """Two songs with this key send the same request (the job asks once; memory only)."""
    return swedebeat.clean_title(title).casefold(), artist.strip().casefold()
