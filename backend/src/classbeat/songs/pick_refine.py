"""The chosen list while its songs are replaced (``pick_analyse``; ADR 0040 amendment "spotdown is
the only analysis path", items 4 and 5; SD-P6 stage B, #1303, #1354).

A song is replaced **alone** by a spare of its role (``song_refine_calc.spare_for``; no model call)
for one of two reasons: its analysis is missing (the reason code) or it does not fit its slot
(``track_fit_policy`` reasons). The spare is analysed in the next round. The record keeps the
original as it was chosen (what an undo needs) and the first reasons.
"""

from collections.abc import Mapping, Sequence

from classbeat.agent.choose_songs import ChosenSong
from classbeat.agent.songs import PoolTrack
from classbeat.jobs import JobFailure
from classbeat.songs.pick_analyse_batch import Want
from classbeat.songs.pick_match import MatchedPool
from classbeat.spotdown.song_analysis_record_model import RefineSwap, SongOutcome
from classbeat.spotdown.song_refine_calc import Slot, spare_for
from classbeat.spotdown.track_fit_policy import Fit

FITS = Fit(True, ())


def slot_of(song: ChosenSong | PoolTrack) -> Slot:
    return Slot(song.spotify_id, song.title, song.artist, song.length_sec)


def want_of(matched: MatchedPool, song: ChosenSong, position: int) -> Want | None:
    """The song's analysis request, or ``None``. ``song.new`` and ``use == "timing"`` are defence in
    depth: a kept song is never matched (stage A leaves it out of the pool), and only a ``timing``
    match carries sources; ``test_a_replace_analyses_only_its_new_song`` runs a replace."""
    match = matched.matches.get(song.spotify_id)
    if not song.new or match is None or match.use != "timing" or not match.sources:
        return None
    explicit = matched.explicit_of(song.spotify_id)
    return Want(position, song.spotify_id, match.sources, float(song.length_sec), explicit)


class Refine:
    """The list while it is refined."""

    def __init__(
        self, matched: MatchedPool, pool: Mapping[str, PoolTrack], songs: list[ChosenSong]
    ) -> None:
        self.matched, self.pool, self.songs = matched, pool, songs
        self.outcomes: dict[int, SongOutcome] = {}
        self.swaps: dict[int, RefineSwap] = {}
        self.tried: set[str] = {s.spotify_id for s in songs}  # never a spare again

    def fits(self) -> list[Fit]:
        """Each position's fit; a song warned, or kept (no outcome), fits."""
        out: list[Fit] = []
        for n in range(len(self.songs)):
            o = self.outcomes.get(n)
            fit = None if o is None or o.warning else o.fit
            out.append(FITS if fit is None else fit)
        return out

    def warn(self, position: int) -> None:
        o = self.outcomes[position]
        self.outcomes[position] = SongOutcome(o.use, o.analysed, o.fit, warning=True)

    def replace(
        self, position: int, round_no: int, target_sec: int, reasons: Sequence[str]
    ) -> Want | None:
        """Swap in a spare and give its ``Want``; ``None``: there is no spare (nothing changed)."""
        song = self.songs[position]
        listed = [s.spotify_id for s in self.songs]
        spares = [self.pool[p.spotify_id] for p in self.matched.spares(song.role, listed)
                  if p.spotify_id in self.pool]  # fmt: skip
        pick = spare_for(
            position, [slot_of(s) for s in self.songs], [slot_of(t) for t in spares],
            self.tried, target_sec,
        )  # fmt: skip
        if pick is None:
            return None
        t = spares[pick]
        was = self.swaps.get(position)
        if was is None:  # the original as it was chosen (what an undo needs)
            self.swaps[position] = RefineSwap(
                song.spotify_id, song.title, song.artist, tuple(reasons), round_no
            )
        else:
            self.swaps[position] = RefineSwap(
                was.original_id, was.title, was.artist, was.reasons, round_no
            )
        new = ChosenSong(t.spotify_id, t.title, t.artist, song.role, t.bpm, t.length_sec, True,
                         t.cover)  # fmt: skip
        self.songs[position] = new
        self.tried.add(t.spotify_id)
        self.outcomes.pop(position, None)
        want = want_of(self.matched, new, position)
        if want is None:  # a spare is a usable candidate: it has a match with sources
            raise JobFailure("internal")
        return want
