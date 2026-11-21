"""The spotdown analysis and refinement stage of a Spotify song pick (ADR 0040 amendment "spotdown
is the only analysis path", items 3 to 5, plan section 1 steps 4 and 5, and section 6; SD-P6 stage
B, #1303, #1354), after ``choose_songs`` and before the list is stored, inside the ``choose``
stage. It always runs: there is no switch, no MOWL-sections timing and no fallback.

1. The job's budget row (``pick_match_budget``, the 600 s the match stage shares): an earlier run of
   this attempt that found spotdown unavailable ends ``analysis_unavailable`` at once.
2. **Only the selected songs** are analysed: each new song of the list (``pick_analyse_batch``;
   nothing of the pool, no kept song).
3. Each document (``pick_outcome``): ``timing_class_policy`` decides the class (what is played is
   Spotify's audio: **at best ``approximate``**; MOWL's sections are anchors,
   ``song_anchors_calc``);
   an ``approximate`` one must map and is checked against its slot (``track_fit_policy``). Any
   other end makes the song **missing**.
4. **Missing songs** are replaced alone by a spare (``pick_refine``), analysed in the next round;
   at most ``song_match_policy.MAX_ROUNDS`` such rounds, no spare or the rounds spent:
   ``analysis_failed``.
5. **Refinement** (``track_fit_policy.plan_round``): at most ``MAX_ROUNDS`` rounds, at most a third
   of the list per round, the worst first; each unsuitable song is replaced alone by a spare. What
   still does not fit after the last round, or has no spare, is accepted with a warning.
6. The record (``song_analysis_record_model``) goes into the list's provenance.

**Outcomes** (item 4): spotdown unreachable (``pick_unavailable``, the category of its reason), a
provider outage (``provider_outage`` over a round's songs) or the budget spent ->
``analysis_unavailable``; any other error is ``internal`` and is not swallowed. Only counts and
codes are logged: never a title, a source, a ref, a key.
"""

import logging
from collections.abc import Mapping
from typing import Any

from classbeat.agent import ChosenList
from classbeat.agent.songs import analysed_pool, total_sec
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs import pick_match_budget as budget
from classbeat.songs import pick_unavailable
from classbeat.songs.pick_analyse_batch import analyse_tracks
from classbeat.songs.pick_match import MatchedPool
from classbeat.songs.pick_match_batch import Session
from classbeat.songs.pick_outcome import Missing, outcome_of
from classbeat.songs.pick_refine import Refine, want_of
from classbeat.songs.seed import Seeded
from classbeat.spotdown import outage_policy, song_match_config, track_fit_policy
from classbeat.spotdown import song_match_policy as match_policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.song_analysis_record_model import analysis_record
from classbeat.spotdown.song_carry_calc import with_kept

logger = logging.getLogger(__name__)


async def _analyse(
    run: JobRun, matched: MatchedPool, analysis_map: Mapping[str, Any], chosen: ChosenList
) -> tuple[ChosenList, dict[str, Any], int]:
    spent, unavailable = await budget.open(run)
    if unavailable:
        raise JobFailure(outage_policy.UNAVAILABLE)  # an earlier run of this attempt found it
    session = Session(run, SpotdownClient(song_match_config.current().sources), spent)
    covers = {p.spotify_id: p.cover for p in matched.playlist}
    pairs = [(p.spotify_id, p.role) for p in matched.eligible]
    pool = {t.spotify_id: t for t in analysed_pool(analysis_map, pairs, covers)[0]}
    state = Refine(matched, pool, list(chosen.songs))
    todo = [w for n, s in enumerate(state.songs) if (w := want_of(matched, s, n)) is not None]
    rounds = fit_rounds = missing_rounds = 0  # ``rounds``: all, for the keys and the record
    while True:
        got = await analyse_tracks(session, todo, rounds)
        errors = sum(g.reason == "provider_error" for g in got.values())
        if match_policy.provider_outage(errors, len(todo)):
            raise await pick_unavailable.unavailable(run)
        gone: dict[int, str] = {}
        for position, g in got.items():
            song = state.songs[position]
            sections = pool[song.spotify_id].sections if song.spotify_id in pool else ()
            made = outcome_of(song, g, sections)
            if isinstance(made, Missing):
                gone[position] = made.reason
            else:
                state.outcomes[position] = made
        todo = []
        if gone:  # item 5: a spare for each, at most ``MAX_ROUNDS`` rounds, then analysis_failed
            if missing_rounds >= match_policy.MAX_ROUNDS:
                raise JobFailure(outage_policy.FAILED)
            missing_rounds += 1
            rounds += 1
            for position, reason in gone.items():
                want = state.replace(position, rounds, chosen.target_sec, (reason,))
                if want is None:
                    raise JobFailure(outage_policy.FAILED)  # no spare for it
                todo.append(want)
            continue
        step = track_fit_policy.plan_round(state.fits(), fit_rounds)
        if step.done:
            for position in step.accept_with_warning:
                state.warn(position)
            break
        fit_rounds += 1
        rounds += 1
        for position in step.replace:
            want = state.replace(
                position, rounds, chosen.target_sec, state.fits()[position].reasons
            )
            if want is None:
                state.warn(position)  # no spare: accepted as it is
            else:
                todo.append(want)
    songs = tuple(state.songs)
    total = round(total_sec([s.length_sec for s in songs]))
    c = chosen
    refined = ChosenList(
        songs, total, c.target_sec, c.crossfade_sec, c.mode, c.omitted_no_bpm, c.note
    )
    return refined, analysis_record(state.outcomes, state.swaps, rounds=rounds), len(state.swaps)


def _log_done(run: JobRun, record: Mapping[str, Any], swaps: int) -> None:
    logger.info(
        "spotdown song analysis",
        extra={
            "job_id": str(run.lease.job_id),
            "analysed": len(record["spotdown_analysis"]["songs"]),
            "swaps": swaps,
            "rounds": record["spotdown_analysis"]["rounds"],
        },
    )


async def analyse_list(
    run: JobRun, matched: MatchedPool, analysis_map: Mapping[str, Any], chosen: ChosenList
) -> tuple[ChosenList, dict[str, Any]]:
    """The list to store and its record; ``JobFailure`` for every ending but a usable list."""
    refined, record, swaps = await _analyse(run, matched, analysis_map, chosen)
    _log_done(run, record, swaps)
    return refined, record


def kept_timing(
    record: dict[str, Any], seeded: Seeded | None, chosen: ChosenList
) -> dict[str, Any]:
    """``record`` with each kept song's spotdown entry of the seed list (SD-P6 stage E,
    ``song_carry_calc``); ``record`` unchanged when there is no seed. An error is not swallowed."""
    if seeded is None:
        return record
    kept = [(k.slot, k.spotify_id) for k in seeded.kept]
    listed = [(s.spotify_id, s.new) for s in chosen.songs]
    return with_kept(record, seeded.provenance, kept, listed)
