"""One song's outcome of the analysis stage (``pick_analyse``; ADR 0040 amendment "spotdown is the
only analysis path", item 5; #1354): its spotdown analysis as a usable timing, or **missing**.

A song is usable only with an ``approximate`` timing that maps (``spotdown_map_policy``). Everything
else is ``Missing(reason)``: no analysis (``Got.reason``), a document that does not read, an
``unusable`` timing class (an anchor ``disagree`` of MOWL's sections included,
``song_anchors_calc``), or a document that does not map. A missing song gets a spare, never MOWL's
sections. A usable one is checked against its slot (``track_fit_policy``).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from classbeat.agent.choose_songs import ChosenSong
from classbeat.songs.pick_analyse_batch import Got
from classbeat.spotdown import track_facts_calc, track_fit_policy
from classbeat.spotdown.explicit_model import document_group
from classbeat.spotdown.provenance_model import provenance_of
from classbeat.spotdown.provenance_read_calc import alignment, analysed, decoded_duration_check
from classbeat.spotdown.song_analysis_policy import slot_need
from classbeat.spotdown.song_analysis_record_model import Analysed, SongOutcome
from classbeat.spotdown.song_anchors_calc import anchor_check
from classbeat.spotdown.spotdown_map_policy import MapRefused, reference_sections
from classbeat.spotdown.timing_class_policy import decide
from classbeat.spotdown.timing_inputs_model import Played, RecordingMatch, TimingInputs


@dataclass(frozen=True, slots=True)
class Missing:
    """The song has no usable timing; ``reason`` is a code (never a title, a source or a key)."""

    reason: str


def outcome_of(
    song: ChosenSong, got: Got, mowl_sections: Sequence[tuple[int, int, float]]
) -> SongOutcome | Missing:
    """A song's outcome from its analysis (``got``); ``mowl_sections``: MOWL's (from, to, energy)
    spans of the song, anchors only."""
    doc, source = got.doc, got.source
    if doc is None or source is None:
        return Missing(got.reason or "no_analysis")
    match = RecordingMatch(
        source.band,
        source.recording_match_confidence,
        source.duration_check,
        decoded_duration_check(doc),
    )
    seen = analysed(doc, None)
    if seen is None:
        return Missing("bad_document")
    played = Played("spotify", song.spotify_id, None)
    anchors = anchor_check(doc, mowl_sections, float(song.length_sec))
    decision = decide(TimingInputs(played, seen, match, alignment(doc), anchors=anchors))
    prov = provenance_of(doc, decision)
    if prov is None:
        return Missing("bad_document")
    if decision.timing_class != "approximate":  # unusable (exact cannot happen: strict anyway)
        return Missing(decision.reason)
    mapped = reference_sections(doc, decision)
    if isinstance(mapped, MapRefused):
        return Missing(mapped.reason)
    record = Analysed(
        prov.analysis_id, (prov.analyzer_name, prov.analyzer_version, prov.config_hash),
        prov.audio, decision, document_group(doc),
    )  # fmt: skip
    facts = track_facts_calc.track_facts(doc, decision)
    fit = track_fit_policy.check(facts, slot_need(song.role, song.bpm, song.length_sec))
    return SongOutcome("spotdown", record, fit)
