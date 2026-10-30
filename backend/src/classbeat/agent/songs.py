"""What the two song stages share (ADR 0008 section 3): the plan the backend computes from the
brief, the analysed tracks the model is shown, and the arithmetic of a song list.

**The plan** (``PickPlan``): ``target_sec`` is the asked length and ``n`` = ``round(target_sec /
240)`` kept in 1..40, both computed here from the brief and told to the model as facts. In replace
mode ``replace`` holds the roles of the slots to fill and ``kept`` the songs that stay.

**The total** (``total_sec``): the sum of the songs' lengths less the crossfade overlap of 9 s per
transition (ADR 0006 section 3), from the analysis; a list is valid when the total is within 10 %
of ``target_sec``. The model never states a length.

Nothing here knows a Spotify id except as an opaque string carried beside the text the model may
see: ``PoolTrack.spotify_id`` and ``KeptSong.spotify_id`` are the backend's (they end up in the
stored ``song_list`` revision) and have no ``repr``; ``model_input`` never reads them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from mowl import spotify

from classbeat.agent import model_input
from classbeat.agent.registry import MAX_TEXT, normalised
from classbeat.agent.schemas import MAX_POOL, MAX_SONGS, Brief

SONG_SEC: Final = 240
CROSSFADE_SEC: Final = float(spotify.CROSSFADE_SEC)
TOLERANCE: Final = 0.10
MAX_MARKS: Final = 6  # drops and breakdowns shown per track
MIN_REPLACE_POOL: Final = 6


@dataclass(frozen=True, slots=True)
class PoolTrack:
    """A pool track as MOWL analysed it, with the role the pool gave it."""

    spotify_id: str = field(repr=False)
    title: str
    artist: str
    role: str
    bpm: float
    length_sec: int
    drops: tuple[float, ...] = ()  # seconds from the start of the track
    breakdowns: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class KeptSong:
    """A song of the seed list that stays (replace mode): its slot (position in the list), role
    and analysed data. It keeps both."""

    slot: int
    spotify_id: str = field(repr=False)
    title: str
    artist: str
    role: str
    bpm: float
    length_sec: int


@dataclass(frozen=True)
class PickPlan:
    brief: Brief
    target_sec: int
    n: int
    replace: tuple[str, ...] = ()  # the roles of the slots to fill; empty: pick mode
    kept: tuple[KeptSong, ...] = ()

    @classmethod
    def build(
        cls, brief: Brief, *, replace: Sequence[str] = (), kept: Sequence[KeptSong] = ()
    ) -> "PickPlan":
        target = brief.length_min * 60
        n = min(MAX_SONGS, max(1, int(target / SONG_SEC + 0.5)))  # half up: 7.5 is 8, 2.5 is 3
        if kept and not replace:
            raise ValueError("kept songs belong to replace mode, which names the slots to fill")
        return cls(brief, target, n, tuple(replace), tuple(kept))

    @property
    def mode(self) -> str:
        return "replace" if self.replace else "pick"

    @property
    def slots(self) -> int:
        """Songs to find: the replaced slots, or the whole class."""
        return len(self.replace) if self.replace else self.n

    @property
    def pool_range(self) -> tuple[int, int]:
        """Fewest and most candidates: n to 4n in pick mode (at most 120), 3 per replaced slot
        (at least 6) to four times that in replace mode."""
        if self.replace:
            low = max(MIN_REPLACE_POOL, 3 * len(self.replace))
            return low, min(MAX_POOL, 4 * low)
        return self.n, min(MAX_POOL, 4 * self.n)

    @property
    def list_length(self) -> int | None:
        """Replace mode keeps every slot: the list is as long as the seed list."""
        return len(self.replace) + len(self.kept) if self.replace else None

    def required_roles(self) -> dict[str, int]:
        """Candidates the pool must hold per role: two per slot of what the brief asks for (its
        climbs, sprints, races and FTP test), and in replace mode two per replaced slot."""
        need: dict[str, int] = {}

        def add(role: str, slots: int) -> None:
            if slots:
                need[role] = need.get(role, 0) + 2 * slots

        if self.replace:
            for role in self.replace:
                add(role, 1)
        else:
            b = self.brief
            add("climb", b.climbs)
            add("sprint", b.sprints)
            add("race", b.races)
            add("interval", 1 if b.ftp_test else 0)
            add("cooldown", 1)  # every class ends on one
        return need


def total_sec(lengths: Sequence[int]) -> float:
    """The class time of songs played in a row: their lengths less 9 s per transition."""
    if not lengths:
        return 0.0
    return float(sum(lengths)) - CROSSFADE_SEC * (len(lengths) - 1)


def within_tolerance(total: float, target_sec: int) -> bool:
    return abs(total - target_sec) <= TOLERANCE * target_sec


def key_of(title: str, artist: str) -> tuple[str, str]:
    return normalised(title), normalised(artist)


def _marks(values: Sequence[float]) -> list[int]:
    return [round(v) for v in sorted(values)[:MAX_MARKS]]


def kept_view(song: KeptSong, index: int) -> dict[str, Any]:
    """A kept song as the model sees it (``index`` in the kept list; no id)."""
    return {
        "index": index,
        "slot": song.slot,
        "title": model_input.view_text(song.title),
        "artist": model_input.view_text(song.artist),
        "role": song.role,
        "bpm": round(song.bpm, 1),
        "length_sec": song.length_sec,
    }


def pool_view(track: PoolTrack, index: int) -> dict[str, Any]:
    """A pool track as ``get_pool`` shows it: MOWL's analysis and the pool's role, no id."""
    return {
        "index": index,
        "title": model_input.view_text(track.title, 100),
        "artist": model_input.view_text(track.artist, 60),
        "role": track.role,
        "bpm": round(track.bpm, 1),
        "length_sec": track.length_sec,
        "drops": _marks(track.drops),
        "breakdowns": _marks(track.breakdowns),
    }


def analysed_pool(
    analysis_map: Mapping[str, Any], wanted: Sequence[tuple[str, str]]
) -> tuple[list[PoolTrack], int]:
    """The pool tracks MOWL analysed, in the order of ``wanted`` (``(spotify_id, role)`` pairs
    from the submitted pool), and how many were left out: a track MOWL does not list, or lists
    without a BPM, is omitted and counted in ``omitted_no_bpm``. Title, artist, BPM and length
    are MOWL's, drops and breakdowns are seconds from the start of the track."""
    m = model_input.music_map(analysis_map)
    by_id: dict[str, Mapping[str, Any]] = {}
    for track in m["tracks"]:
        by_id.setdefault(str(track.get("spotify_id")), track)
    starts: dict[int, float] = {}
    for section in m["songs"]:
        starts.setdefault(int(section["track"]), float(section["start"]))
    sections_of: dict[int, set[int]] = {}
    for section in m["songs"]:
        sections_of.setdefault(int(section["track"]), set()).add(int(section["song"]))
    out: list[PoolTrack] = []
    omitted = 0
    for spotify_id, role in wanted:
        track = by_id.get(spotify_id)
        bpm = track.get("bpm") if track else None
        if (
            track is None
            or isinstance(bpm, bool)
            or not isinstance(bpm, int | float)
            or bpm <= 0
            or float(track["sec"]) < 1
        ):
            omitted += 1
            continue
        number = int(track["track"])
        start = starts.get(number, 0.0)
        mine = sections_of.get(number, set())
        marks = [e for e in m["events"] if int(e["song"]) in mine]
        out.append(
            PoolTrack(
                spotify_id=spotify_id,
                title=model_input.view_text(track.get("title"), MAX_TEXT),
                artist=model_input.view_text(track.get("artist"), MAX_TEXT),
                role=role,
                bpm=float(bpm),
                length_sec=round(float(track["sec"])),
                drops=tuple(float(e["t"]) - start for e in marks if e["kind"] == "drop"),
                breakdowns=tuple(float(e["t"]) - start for e in marks if e["kind"] == "breakdown"),
            )
        )
    return out, omitted
