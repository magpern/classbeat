"""The spotdown analysis and refinement stage of a Spotify song pick (ADR 0040 sections 2, 3, 6
and 8, plan section 1 steps 4 and 5, and section 6; SD-P6 stage B, #1303), after ``choose_songs``
and before the list is stored, inside the ``choose`` stage.

Inactive (the match did not run for this class: off, not allowlisted, or it fell back) nothing
here is called and ``analyse_list`` gives the chosen list back unchanged with no record.

Active:

1. The job's budget row (``pick_match_budget``, the 600 s the match stage shares): an earlier run
   that fell back means today's path.
2. **Only the selected songs** are analysed: each new song of the list whose match is ``timing``,
   by its sources (``pick_analyse_batch``; nothing of the pool, no kept song).
3. Each document: ``timing_class_policy`` decides the class (what is played is Spotify's audio:
   **at best ``approximate``**); an ``unusable`` one is discarded and the song keeps MOWL's
   sections; an ``approximate`` one must map (``spotdown_map_policy.reference_sections``) and is
   checked against its slot (``track_facts_calc``, ``song_analysis_policy.slot_need``,
   ``track_fit_policy.check``).
4. **Refinement** (``track_fit_policy.plan_round``): at most ``MAX_ROUNDS`` rounds, at most a
   third of the list per round, the worst first; each unsuitable song is replaced **alone** by a
   spare (``song_refine_calc.spare_for``; no model call), which is analysed in the next round.
   What still does not fit after the last round, or has no spare, is accepted with a warning.
5. The record (``song_analysis_record_model``) goes into the list's provenance.

**Fallback to today's path** for the whole class (the list as chosen, no record; every later run
of the job too): spotdown unreachable (any call), a provider outage (``provider_outage`` over a
round's songs: no swaps), or **an error of any type** in here (swallowed; only its type is
logged). Only ``RunStopped``, ``LeaseLost`` and ``DatabaseRoleError`` pass. Only counts and
codes are logged: never a title, a source, a ref, a key.
"""

import logging
from collections.abc import Mapping
from typing import Any

from classbeat.agent import ChosenList
from classbeat.agent.choose_songs import ChosenSong
from classbeat.agent.songs import PoolTrack, analysed_pool, total_sec
from classbeat.jobs import JobRun
from classbeat.songs import pick_match_budget as budget
from classbeat.songs.pick_analyse_batch import Got, Want, analyse_tracks
from classbeat.songs.pick_match import PASSED, MatchedPool
from classbeat.songs.pick_match_batch import Fallback, Session
from classbeat.songs.seed import Seeded
from classbeat.spotdown import song_match_config, track_facts_calc, track_fit_policy
from classbeat.spotdown import song_match_policy as match_policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.provenance_model import provenance_of
from classbeat.spotdown.provenance_read_calc import alignment, analysed, decoded_duration_check
from classbeat.spotdown.song_analysis_policy import slot_need
from classbeat.spotdown.song_analysis_record_model import (
    Analysed,
    RefineSwap,
    SongOutcome,
    analysis_record,
)
from classbeat.spotdown.song_carry_calc import with_kept
from classbeat.spotdown.song_refine_calc import Slot, spare_for
from classbeat.spotdown.spotdown_map_policy import MapRefused, reference_sections
from classbeat.spotdown.timing_class_policy import decide
from classbeat.spotdown.timing_inputs_model import Played, RecordingMatch, TimingInputs
from classbeat.spotdown.track_fit_policy import Fit

logger = logging.getLogger(__name__)

FITS = Fit(True, ())


def outcome_of(song: ChosenSong, got: Got) -> SongOutcome:
    """A song's outcome from its analysis (``got``); MOWL's sections unless it is usable."""
    doc, source = got.doc, got.source
    if doc is None or source is None:
        return SongOutcome("sections", got.reason or "no_analysis")
    match = RecordingMatch(
        source.band,
        source.recording_match_confidence,
        source.duration_check,
        decoded_duration_check(doc),
    )
    seen = analysed(doc, None)
    if seen is None:
        return SongOutcome("sections", "bad_document")
    played = Played("spotify", song.spotify_id, None)
    decision = decide(TimingInputs(played, seen, match, alignment(doc)))
    prov = provenance_of(doc, decision)
    if prov is None:
        return SongOutcome("sections", "bad_document")
    record = Analysed(
        prov.analysis_id, (prov.analyzer_name, prov.analyzer_version, prov.config_hash),
        prov.audio, decision,
    )  # fmt: skip
    if decision.timing_class != "approximate":  # unusable (exact cannot happen: strict anyway)
        return SongOutcome("sections", decision.reason, record)
    mapped = reference_sections(doc, decision)
    if isinstance(mapped, MapRefused):
        return SongOutcome("sections", mapped.reason, record)
    facts = track_facts_calc.track_facts(doc, decision)
    fit = track_fit_policy.check(facts, slot_need(song.role, song.bpm, song.length_sec))
    return SongOutcome("spotdown", None, record, fit)


def _want(matched: MatchedPool, song: ChosenSong, position: int) -> Want | None:
    """The song's analysis request, or ``None``. ``song.new`` and ``use == "timing"`` are defence in
    depth: a kept song is never matched (stage A leaves it out of the pool), and only a ``timing``
    match carries sources; ``test_a_replace_analyses_only_its_new_song`` runs a replace."""
    match = matched.matches.get(song.spotify_id)
    if not song.new or match is None or match.use != "timing" or not match.sources:
        return None
    return Want(position, song.spotify_id, match.sources, float(song.length_sec))


def _slot(song: ChosenSong | PoolTrack) -> Slot:
    return Slot(song.spotify_id, song.title, song.artist, song.length_sec)


class _Refine:
    """The list while it is refined."""

    def __init__(
        self, matched: MatchedPool, pool: Mapping[str, PoolTrack], songs: list[ChosenSong]
    ) -> None:
        self.matched, self.pool, self.songs = matched, pool, songs
        self.outcomes: dict[int, SongOutcome] = {}
        self.swaps: dict[int, RefineSwap] = {}
        self.tried: set[str] = {s.spotify_id for s in songs}  # never a spare again

    def fits(self) -> list[Fit]:
        """Each position's fit; a song not on spotdown's timing, or warned, fits."""
        out: list[Fit] = []
        for n in range(len(self.songs)):
            o = self.outcomes.get(n)
            fit = None if o is None or o.use != "spotdown" or o.warning else o.fit
            out.append(FITS if fit is None else fit)
        return out

    def warn(self, position: int) -> None:
        o = self.outcomes[position]
        self.outcomes[position] = SongOutcome(o.use, o.reason, o.analysed, o.fit, warning=True)

    def replace(self, position: int, round_no: int, target_sec: int) -> Want | bool | None:
        """Swap in a spare: its ``Want`` (or ``None`` when it has no source); ``False``: none."""
        song = self.songs[position]
        listed = [s.spotify_id for s in self.songs]
        spares = [self.pool[p.spotify_id] for p in self.matched.spares(song.role, listed)
                  if p.spotify_id in self.pool]  # fmt: skip
        pick = spare_for(
            position, [_slot(s) for s in self.songs], [_slot(t) for t in spares],
            self.tried, target_sec,
        )  # fmt: skip
        if pick is None:
            return False
        t = spares[pick]
        fit = self.outcomes[position].fit
        if position not in self.swaps:  # the original as it was chosen (what an undo needs)
            reasons = fit.reasons if fit is not None else ()
            self.swaps[position] = RefineSwap(
                song.spotify_id, song.title, song.artist, reasons, round_no
            )
        else:
            was = self.swaps[position]
            self.swaps[position] = RefineSwap(
                was.original_id, was.title, was.artist, was.reasons, round_no
            )
        new = ChosenSong(t.spotify_id, t.title, t.artist, song.role, t.bpm, t.length_sec, True,
                         t.cover)  # fmt: skip
        self.songs[position] = new
        self.tried.add(t.spotify_id)
        del self.outcomes[position]
        want = _want(self.matched, new, position)
        if want is None:  # a spare MOWL's sections serve (today's path for it)
            self.outcomes[position] = SongOutcome("sections", "not_analysed")
        return want


async def _analyse(
    run: JobRun, matched: MatchedPool, analysis_map: Mapping[str, Any], chosen: ChosenList
) -> tuple[ChosenList, dict[str, Any], int]:
    spent, fallen = await budget.open(run)
    if fallen:
        raise Fallback("earlier_run")
    session = Session(run, SpotdownClient(song_match_config.current().sources), spent)
    covers = {p.spotify_id: p.cover for p in matched.playlist}
    pairs = [(p.spotify_id, p.role) for p in matched.eligible]
    pool = {t.spotify_id: t for t in analysed_pool(analysis_map, pairs, covers)[0]}
    state = _Refine(matched, pool, list(chosen.songs))
    todo = [w for n, s in enumerate(state.songs) if (w := _want(matched, s, n)) is not None]
    rounds = 0
    while True:
        got = await analyse_tracks(session, todo, rounds)
        errors = sum(g.reason == "provider_error" for g in got.values())
        if match_policy.provider_outage(errors, len(todo)):
            raise Fallback("provider_outage")
        for position, g in got.items():
            state.outcomes[position] = outcome_of(state.songs[position], g)
        step = track_fit_policy.plan_round(state.fits(), rounds)
        if step.done:
            for position in step.accept_with_warning:
                state.warn(position)
            break
        rounds += 1
        todo = []
        for position in step.replace:
            made = state.replace(position, rounds, chosen.target_sec)
            if made is False:
                state.warn(position)  # no spare: accepted as it is
            elif isinstance(made, Want):
                todo.append(made)
    songs = tuple(state.songs)
    total = round(total_sec([s.length_sec for s in songs]))
    c = chosen
    refined = ChosenList(
        songs, total, c.target_sec, c.crossfade_sec, c.mode, c.omitted_no_bpm, c.note
    )
    return refined, analysis_record(state.outcomes, state.swaps, rounds=rounds), len(state.swaps)


async def _mark_fallback(run: JobRun) -> None:
    try:
        await budget.mark_fallback(run)
    except PASSED:
        raise
    except Exception as exc:  # the fallback holds for this run anyway
        kind = type(exc).__name__
        logger.warning("spotdown song analysis fallback not recorded", extra={"error_type": kind})


def _log_done(run: JobRun, record: Mapping[str, Any], swaps: int) -> None:
    songs = record["spotdown_analysis"]["songs"]
    logger.info(
        "spotdown song analysis",
        extra={
            "job_id": str(run.lease.job_id),
            "analysed": sum(s["use"] == "spotdown" for s in songs),
            "sections": sum(s["use"] == "sections" for s in songs),
            "swaps": swaps,
            "rounds": record["spotdown_analysis"]["rounds"],
        },
    )


async def analyse_list(
    run: JobRun, matched: MatchedPool, analysis_map: Mapping[str, Any], chosen: ChosenList
) -> tuple[ChosenList, dict[str, Any]]:
    """The list to store and its record; ``chosen`` and ``{}`` when inactive or on any
    fallback."""
    if not matched.active:
        return chosen, {}
    try:
        refined, record, swaps = await _analyse(run, matched, analysis_map, chosen)
        _log_done(run, record, swaps)
    except PASSED:
        raise
    except Exception as exc:
        reason = exc.reason if isinstance(exc, Fallback) else "internal"
        if not isinstance(exc, Fallback):
            kind = type(exc).__name__
            logger.warning("spotdown song analysis failed", extra={"error_type": kind})
        if reason != "earlier_run":
            await _mark_fallback(run)
        logger.info(
            "spotdown song analysis fallback",
            extra={"job_id": str(run.lease.job_id), "reason": reason},
        )
        return chosen, {}
    return refined, record


def kept_timing(
    record: dict[str, Any], seeded: Seeded | None, chosen: ChosenList
) -> dict[str, Any]:
    """``record`` with each kept song's spotdown entry of the seed list (SD-P6 stage E,
    ``song_carry_calc``); ``record`` unchanged when there is no seed, no record, or on an error of
    any type (the kept songs then keep MOWL's sections). Only ``RunStopped``, ``LeaseLost`` and
    ``DatabaseRoleError`` pass, as everywhere (none can come from pure code)."""
    if seeded is None or not record:
        return record
    try:
        kept = [(k.slot, k.spotify_id) for k in seeded.kept]
        listed = [(s.spotify_id, s.new) for s in chosen.songs]
        return with_kept(record, seeded.provenance, kept, listed)
    except PASSED:
        raise
    except Exception as exc:
        kind = type(exc).__name__
        logger.warning("spotdown kept timing failed", extra={"error_type": kind})
        return record
