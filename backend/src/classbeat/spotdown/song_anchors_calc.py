"""MOWL's sections of a Spotify song as anchors for the timing class (ADR 0040 amendment "spotdown
is the only analysis path", item 3; #1354). Pure: no I/O.

MOWL's playlist map stays the frame of a Spotify class and its sections stay **anchors**: the start
of each MOWL section (seconds from the start of the track, Spotify's clock) is looked for among
spotdown's segment boundaries moved to that clock (``anchor_calc``). The check feeds
``timing_class_policy`` as ``TimingInputs.anchors``; a ``disagree`` makes the track ``unusable``,
so the song is missing (item 5). MOWL's sections are never a song's timing here.

A song MOWL has no sections for, or too few of them (``anchor_calc.MIN_ANCHORS``), is
``inconclusive``: it neither confirms nor refutes.
"""

from collections.abc import Mapping, Sequence

from classbeat.spotdown import analysis_features_calc as fc
from classbeat.spotdown.anchor_calc import agreement, check
from classbeat.spotdown.provenance_read_calc import alignment
from classbeat.spotdown.timing_inputs_model import AnchorCheck


def anchor_check(
    doc: Mapping[str, object],
    mowl_sections: Sequence[tuple[int, int, float]],
    reference_duration_s: float,
) -> AnchorCheck:
    """The anchor check of ``doc`` against MOWL's ``sections`` (from, to, energy) of the track."""
    duration = fc.read_duration(doc)
    moved = alignment(doc)
    if duration is None or moved is None or moved.offset_s is None:
        return "inconclusive"
    boundaries = [edge for span in fc.section_spans(doc, duration) for edge in span]
    anchors = [float(start) for start, _end, _energy in mowl_sections]
    return check(agreement(anchors, boundaries, moved.offset_s, reference_duration_s))
