"""The ``POST /v2/analyses`` requests and ``GET /v2/jobs/{id}`` polls of a song pick's analysis
stage (``pick_analyse``; ADR 0040 sections 2 and 8; SD-P6 stage B, #1303), for a set of the
chosen list's songs at once, with the reactions of ``song_analysis_policy``.

- Each song is asked for by its match's source (``youtube:<id>``, the best first, then its
  alternates), **with no ``cache_scope``** (a ``youtube`` analysis is global) and with a
  ``spotify`` reference for the alignment: its length on Spotify's clock and an **opaque id**
  (a digest of the Spotify id; no Spotify id leaves). A cache hit is ready at once.
- The songs go in **rounds**: every song that has a request to make makes it, every queued job is
  polled, then the run waits once, for the longest pause any of them asked for (a retry pause, a
  ``rate_limited`` wait, the poll pace of ``poll_pace_calc.poll_delay``). **Every wait is charged
  to the job's budget before it is made** (``pick_match_batch.wait``, the budget the match stage
  shares) and made only when it fits what is left; a wait that does not fit ends the job
  ``analysis_unavailable`` (the budget is spent, item 4).
- **Idempotency-Key**: from the job, the refinement round, the position, the request's number for
  that song and the source asked (hashed). ``client_ref`` is the position.
- A document is used only when it is the analysis asked for (its id, a ``youtube`` source with
  the video id asked).
- spotdown unreachable (any reason, any call) raises the ``JobFailure`` of
  ``pick_unavailable.unreachable`` (the category of its reason; its fixed line is logged at every
  site: the start, a poll and the read of the document). A song that cannot be analysed ends as
  ``Got(reason=...)``: the caller makes it missing (item 5).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from classbeat.songs import pick_unavailable
from classbeat.songs.pick_match_batch import Session, wait
from classbeat.spotdown import song_analysis_policy as policy
from classbeat.spotdown import song_match_policy
from classbeat.spotdown.failure_policy import AnalysisReady, Pending, TrackFailure, Unreachable
from classbeat.spotdown.match_model import Candidate
from classbeat.spotdown.poll_pace_calc import poll_delay
from classbeat.spotdown.provenance_read_calc import youtube_ref
from classbeat.spotdown.request_model import Reference
from classbeat.spotdown.scope_calc import idempotency_key
from classbeat.spotdown.song_sections_calc import REFERENCE_KIND, reference_id


@dataclass(frozen=True, slots=True)
class Want:
    """One song of the list to analyse."""

    position: int
    spotify_id: str = field(repr=False)
    sources: tuple[Candidate, ...] = field(repr=False)
    length_s: float = 0.0  # on Spotify's clock
    explicit: bool | None = None  # Spotify's flag, sent only when known (#1340)


@dataclass(frozen=True, slots=True)
class Got:
    """A song's end: its document and the source it came from, or why it is missing."""

    doc: dict[str, object] | None = field(default=None, repr=False)
    source: Candidate | None = field(default=None, repr=False)
    reason: str | None = None


@dataclass
class _Track:
    want: Want
    tried: policy.Tried = field(default_factory=policy.Tried)
    job_id: str | None = None
    asks: bool = True  # a request is due this round


def reference_of(want: Want) -> Reference:
    return Reference(REFERENCE_KIND, reference_id(want.spotify_id), float(want.length_s))


def _key(session: Session, round_no: int, track: _Track) -> str:
    source = track.want.sources[track.tried.source].source
    parts = (f"r{round_no}", f"p{track.want.position}", f"q{track.tried.requests}", source)
    return idempotency_key("song_analysis", str(session.run.lease.job_id), *parts)


async def _read(session: Session, track: _Track, analysis_id: str) -> Got | TrackFailure:
    doc = await session.client.get_analysis(analysis_id)  # global: no scope
    if isinstance(doc, Unreachable):
        raise await pick_unavailable.unreachable(session.run, doc)
    if isinstance(doc, TrackFailure):
        return doc
    source = track.want.sources[track.tried.source]
    if doc.get("analysis_id") != analysis_id or f"youtube:{youtube_ref(doc)}" != source.source:
        return Got(reason="bad_document")
    return Got(doc, source)


async def _step(session: Session, round_no: int, track: _Track) -> Got | TrackFailure | None:
    """The song's request or poll this round: its end, a failure to react to, or ``None``
    (still queued)."""
    if track.asks or track.job_id is None:
        track.asks = False
        track.tried = policy.Tried(
            track.tried.source,
            track.tried.requests + 1,
            track.tried.retried_error,
            track.tried.retried_timeout,
        )
        started = await session.client.start_analysis(
            None,
            f"p{track.want.position}",
            _key(session, round_no, track),
            source_ref=track.want.sources[track.tried.source].source,
            reference=reference_of(track.want),
            explicit=track.want.explicit,
        )
    else:
        started = await session.client.poll(track.job_id)
    if isinstance(started, Unreachable):
        raise await pick_unavailable.unreachable(session.run, started)
    if isinstance(started, Pending):
        track.job_id = started.job_id
        return None
    track.job_id = None
    if isinstance(started, AnalysisReady):
        return await _read(session, track, started.analysis_id)
    return started


async def analyse_tracks(session: Session, wants: Sequence[Want], round_no: int) -> dict[int, Got]:
    """Each song's end, by position."""
    open_ = {w.position: _Track(w) for w in wants if w.sources}
    ends: dict[int, Got] = {w.position: Got(reason="no_source") for w in wants if not w.sources}
    polls = 0
    while open_:
        pause = 0.0
        queued = False
        for position, track in list(open_.items()):
            await session.run.checkpoint()
            result = await _step(session, round_no, track)
            if result is None:
                queued = True
                continue
            if isinstance(result, Got):
                ends[position] = result
                del open_[position]
                continue
            reaction = policy.react(result, track.tried, len(track.want.sources))
            track.tried = reaction.tried
            if reaction.step == "missing":
                ends[position] = Got(reason=reaction.reason)
                del open_[position]
                continue
            track.asks = True
            pause = max(pause, reaction.pause)
        if not open_:
            break
        if queued:
            pause = max(pause, poll_delay(polls, None, song_match_policy.WAIT_SECONDS))
            polls += 1
        pause_s = song_match_policy.wait_for(pause, session.left())
        if pause_s is None:
            raise await pick_unavailable.unavailable(session.run)  # the budget is spent
        if pause_s > 0:
            await wait(session, pause_s)
    return ends
