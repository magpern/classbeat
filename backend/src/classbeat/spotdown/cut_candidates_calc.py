"""The cut candidates of an ``analysis-v1`` document (#1328; #580's approved design, ADR 0040
sections 3 and 5). Pure: no I/O. Facts only, no workout logic: where a track may be cut, never
where it should be.

``points`` reads every musical point on the **file clock**: the content start and end
(``signal``), the edges of each silence region and of the fade-in and fade-out, each segment's
start (``structure``) and, at the same time, the start of a labelled segment (a hint: labels are
always ``heuristic``), each phrase's start and end, and the downbeats (``grid``). Each point
carries its feature's ``confidence`` and ``method``.

``candidates`` maps them to the played clock, ``reference_time = file_time + offset_s``, and sets
``trusted``. **Trust is inherited, never set by the kind of point**: a candidate is trusted only
when its feature passes ``feature_trust_policy`` **and** the track's timing decision is ``exact``.

- ``exact`` on path (a) (``played_recording``): the file is the played recording, so the offset
  is 0;
- ``exact`` on path (b): v1 carries no offset for it (``spotdown_map_policy``), so none;
- ``approximate``: the alignment's ``offset_s``, which must equal the decision's evidence when
  that has one; every candidate is advisory (``trusted`` false);
- ``unusable``: none at all.

**Any doubt refuses**: no offset, an offset that disagrees with the evidence, no reference length,
or more than ``MAX_CANDIDATES`` points gives no candidates. A point outside the played track is
left out.
"""

from collections.abc import Mapping
from typing import Final

from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.cut_candidates_model import LABELS, Candidate
from classbeat.spotdown.timing_class_policy import TimingDecision

VERSION: Final = "1"  # in the studio provenance's policy versions (``studio_map_model``)
MAX_CANDIDATES: Final = 5000
MAX_OFFSET_S: Final = 3600.0
Document = Mapping[str, object]


def _point(
    at: object, kind: str, feature: Mapping[str, object] | None, key: str, label: object = None
) -> list[Candidate]:
    time = ft.num(at, 0.0)
    if time is None or feature is None:
        return []
    method = feature.get("method")
    return [Candidate(
        time, kind, ft.confidence(feature, key),
        method if isinstance(method, str) and method in ft.METHODS else None,
        ft.trusted(feature, key),
        label if isinstance(label, str) and label in LABELS else None,
    )]  # fmt: skip


def _edges(feature: Mapping[str, object], kind: str) -> list[Candidate]:
    start = _point(feature.get("start_s"), f"{kind}_start", feature, "confidence")
    return start + _point(feature.get("end_s"), f"{kind}_end", feature, "confidence")


def points(doc: Document) -> list[Candidate]:
    """Every point of the document on the file clock, ``trusted`` by its feature alone."""
    out: list[Candidate] = []
    signal = ft.obj(doc, "signal") or {}
    out += _point(signal.get("content_start_s"), "content_start", signal, "confidence")
    out += _point(signal.get("content_end_s"), "content_end", signal, "confidence")
    for region in ft.items(signal.get("silence_regions")):
        out += _edges(region, "silence")
    for name in ("fade_in", "fade_out"):
        fade = ft.obj(signal, name)
        out += [] if fade is None else _edges(fade, name)
    structure = ft.obj(doc, "structure") or {}
    starts: dict[str, object] = {}
    for segment in ft.items(structure.get("segments")):
        out += _point(segment.get("start_s"), "segment_start", segment, "boundary_confidence")
        if (sid := ft.word(segment.get("id"))) is not None:
            starts.setdefault(sid, segment.get("start_s"))
    for label in ft.items(doc.get("labels")):  # always a hint (``analysis_hints_policy``)
        sid, heuristic = ft.word(label.get("segment_id")), {**label, "method": "heuristic"}
        at = None if sid is None else starts.get(sid)
        out += _point(at, "label_start", heuristic, "confidence", label.get("label"))
    grid = ft.obj(doc, "grid") or {}
    for phrase in ft.items(grid.get("phrases")):
        out += _edges(phrase, "phrase")
    downbeats = ft.obj(grid, "downbeats")
    for at in (None if downbeats is None else ft.numbers(downbeats.get("times_s"))) or []:
        out += _point(at, "downbeat", downbeats, "confidence")
    return out


def offset_s(doc: Document, decision: TimingDecision) -> tuple[float, float] | None:
    """``(offset_s, played length)`` for the clock mapping, or ``None`` when in doubt."""
    signal = ft.obj(doc, "signal")
    duration = None if signal is None else ft.num(signal.get("duration_s"), 0.001, 24 * 3600)
    if decision.timing_class == "exact":
        ok = decision.reason == "played_recording" and duration is not None
        return (0.0, duration) if ok and duration is not None else None
    if decision.timing_class != "approximate":
        return None
    alignment = ft.obj(doc, "alignment")
    reference = ft.obj(alignment, "reference")
    offset = None if alignment is None else ft.num(alignment.get("offset_s"))
    length = None if reference is None else ft.num(reference.get("duration_s"), 0.001, 7200.0)
    if offset is None or length is None or not -MAX_OFFSET_S <= offset <= MAX_OFFSET_S:
        return None
    if "offset_s" in decision.evidence and ft.num(decision.evidence["offset_s"]) != offset:
        return None  # the stored decision rests on another offset
    return offset, length


def candidates(doc: Document, decision: TimingDecision) -> tuple[Candidate, ...]:
    """The points on the played clock with their inherited trust, in time order."""
    mapping = offset_s(doc, decision)
    found = points(doc)
    if mapping is None or len(found) > MAX_CANDIDATES:
        return ()
    offset, length = mapping
    exact = decision.timing_class == "exact"
    out = [
        Candidate(round(c.at_s + offset, 3), c.kind, c.confidence, c.method,
                  c.trusted and exact, c.label)
        for c in found
        if 0.0 <= c.at_s + offset <= length
    ]  # fmt: skip
    return tuple(sorted(out, key=lambda c: (c.at_s, c.kind)))
