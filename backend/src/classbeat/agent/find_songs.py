"""``pick_pool``'s ``find_songs`` tool (issue #1133): songs the model knows, verified on Spotify.

The model proposes songs from its own knowledge (an artist and a ``track``, the title, each, at most
``MAX_SONGS`` per call) and, for a stated era, the era's years; the backend runs one Spotify search
per song (``track:"<title>" artist:"<artist>"`` and ``year:`` when years are given, built here from
the cleaned words: the model supplies no filter syntax) through the ``Searcher`` the stage was lent,
and keeps a song only when a hit **is** that song (``song_match_policy``: the same artist, the same
title, not a live or cover version, within the years). The first such hit the job's
``SearchRegistry`` accepts (a later one when it refuses one, issue #1140) comes back with its
``ref``; a song without a match is only listed as missing (its position in the call), and nothing
of it can enter the pool. So the pool never holds a song the model remembered but Spotify does not
have.

The answer is ``{found: [{song, ref, title, artist, album, year, duration_sec}], missing: [song],
candidates_found}``; the text is Spotify's, cleaned by the registry. A search that cannot run is the
same fixed ``upstream_unavailable`` sentence as ``search_tracks``. A call sends one search per song,
counted against the stage's ``SearchBudget``; a call over it sends nothing (``limit_exceeded``).
"""

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, Final, Protocol, Self

from pydantic import AfterValidator, BaseModel, Field, model_validator

from classbeat.agent.registry import SearchRegistry
from classbeat.agent.schemas import SEARCH_DROPPED, STRICT, clean_text
from classbeat.agent.search_budget import SearchBudget
from classbeat.agent.song_match_policy import matching_hits
from classbeat.agent.stage import Tool, ToolAnswer, ToolError

NAME: Final = "find_songs"
MAX_SONGS: Final = 10
MAX_RESULTS: Final = 10

DESCRIPTION: Final = (
    "Find songs you know on Spotify: songs (artist and track, the song's title, each; at most 10) "
    "and, for one era, "
    "year_from and year_to. Each song is searched by its artist and title and kept only if "
    "Spotify has that exact song (same artist and title, the original version, within the "
    "years). Returns found (song: its position in your list, ref, title, artist, album, year, "
    "duration_sec) and missing (positions Spotify did not have). Use a ref to put a track in "
    "the pool; a missing song cannot be used."
)

Hits = Sequence[Mapping[str, Any]]


class Source(Protocol):
    """Where the tool reads: the stage's registry and the search it was lent (``PoolContext``),
    looked up at call time only, so a stage built for its prompt version needs neither."""

    @property
    def registry(self) -> SearchRegistry: ...
    @property
    def searcher(self) -> Callable[[str], Hits | Awaitable[Hits]]: ...


def _words(value: str) -> str:
    """Plain words: control and format characters, Spotify's filter and quote syntax removed."""
    words = clean_text(value).translate(SEARCH_DROPPED).split()
    if not words:
        raise ValueError("must have visible words")
    return " ".join(words)


class WantedSong(BaseModel):
    model_config = STRICT

    artist: Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_words)]
    # The song's title, named ``track`` while the strict tool form dropped a property named
    # ``title`` (the JSON schema's own keyword); #1197 fixed that, the name stays.
    track: Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_words)]


class FindSongsInput(BaseModel):
    """``find_songs``: songs by artist and title, and the years of one era."""

    model_config = STRICT

    songs: list[WantedSong] = Field(min_length=1, max_length=MAX_SONGS)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def _years(self) -> Self:
        low, high = self.year_from, self.year_to
        if low is not None and high is not None and low > high:
            raise ValueError("year_from must not be after year_to")
        return self

    def years(self) -> tuple[int, int] | None:
        if self.year_from is None and self.year_to is None:
            return None
        low = self.year_from if self.year_from is not None else 1900
        return low, self.year_to if self.year_to is not None else 2100


def song_query(song: WantedSong, years: tuple[int, int] | None) -> str:
    """The Spotify ``q`` the backend builds for one song."""
    query = f'track:"{song.track}" artist:"{song.artist}"'
    if years is None:
        return query
    low, high = years
    return f"{query} year:{low}" if low == high else f"{query} year:{low}-{high}"


def find_songs_tool(
    source: Source,
    unavailable: type[Exception],
    refused: Callable[[], ToolError],
    budget: SearchBudget,
) -> Tool:
    """The tool, reading through ``source.searcher``; ``unavailable`` is the searcher's "could not
    run" exception, answered with ``refused()`` (the stage's fixed sentence). ``budget`` is the
    stage's searches (``search_budget``): a call it cannot hold sends nothing."""

    def answer(args: FindSongsInput, results: Sequence[Hits]) -> ToolAnswer:
        registry = source.registry
        years = args.years()
        found: list[dict[str, Any]] = []
        missing: list[int] = []
        for n, (song, hits) in enumerate(zip(args.songs, results, strict=True)):
            entry = None
            for hit in matching_hits(song.artist, song.track, list(hits)[:MAX_RESULTS], years):
                entries = registry.add_hits([hit])  # one at a time: a rejected hit tries the next
                if entries:
                    entry = entries[0]
                    break
            if entry is not None:
                found.append({"song": n, **entry.shown()})
            else:
                missing.append(n)
        if not found and registry.full:
            raise ToolError(
                "limit_exceeded", ["the candidate list is full; build the pool from what you have"]
            )
        return ToolAnswer({"found": found, "missing": missing, "candidates_found": len(registry)})

    async def finish(args: FindSongsInput, pending: list[Hits | Awaitable[Hits]]) -> ToolAnswer:
        results: list[Hits] = []
        try:
            for at, item in enumerate(pending):
                if inspect.isawaitable(item):
                    try:
                        item = await item
                    except BaseException:
                        _close(pending[at + 1 :])
                        raise
                results.append(item)
        except unavailable:
            raise refused() from None
        return answer(args, results)

    def handler(args: FindSongsInput) -> ToolAnswer | Awaitable[ToolAnswer]:
        years = args.years()
        budget.take(len(args.songs))
        pending: list[Hits | Awaitable[Hits]] = []
        try:
            for song in args.songs:
                pending.append(source.searcher(song_query(song, years)))
        except unavailable:
            _close(pending)
            raise refused() from None
        except BaseException:
            _close(pending)
            raise
        if any(inspect.isawaitable(p) for p in pending):
            return finish(args, pending)
        return answer(args, [p for p in pending if not inspect.isawaitable(p)])

    return Tool(NAME, "read", DESCRIPTION, FindSongsInput, handler)


def _close(pending: Sequence[Any]) -> None:
    """Close the searches not yet awaited, so none is left unrun and unclosed."""
    for item in pending:
        close = getattr(item, "close", None)
        if callable(close):
            close()
