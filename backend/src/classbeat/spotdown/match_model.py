"""spotdown's ``match-v1`` results (``POST /v2/match``), parsed strictly. Pure: no I/O.

``best`` only for ``matched`` and ``uncertain`` (with ``matcher_version``), ``retry_after_s`` only
for ``rate_limited``, at most 2 ``alternates``, a ``band`` that agrees with its confidence, a
``cached_summary`` only with a ``cached_analysis_id``, and no field the schema does not name. The
batch answer must hold one result per requested ``client_ref``, in request order.
"""

from dataclasses import dataclass
from typing import Final

from classbeat.spotdown.answer_model import checked_id, json_object, number, text

MATCH_STATUSES: Final = frozenset(
    {"matched", "not_found", "uncertain", "source_unavailable", "provider_error", "rate_limited"}
)
_BANDS: Final = {"confident": (0.85, 1.0), "uncertain": (0.6, 0.85), "not_same": (0.0, 0.6)}
_CHECKS: Final = frozenset({"ok", "tolerable", "rejected"})
_SUMMARY_KEYS: Final = frozenset(
    {"analyzer_version", "bpm", "bpm_confidence", "duration_s", "has_beats"}
)
_SUMMARY_FLAGS: Final = ("has_beats", "has_downbeats", "has_events")
_CANDIDATE_KEYS: Final = frozenset(
    {
        "source",
        "recording_match_confidence",
        "band",
        "duration_s",
        "duration_delta_s",
        "duration_check",
    }
)


@dataclass(frozen=True, slots=True)
class CachedSummary:
    analyzer_version: str
    bpm: float
    bpm_confidence: float
    duration_s: float
    has_beats: bool
    has_downbeats: bool | None
    has_events: bool | None


@dataclass(frozen=True, slots=True)
class Candidate:
    source: str  # ``<kind>:<ref>``
    recording_match_confidence: float
    band: str
    duration_s: float
    duration_delta_s: float
    duration_check: str
    cached_analysis_id: str | None
    cached_summary: CachedSummary | None


@dataclass(frozen=True, slots=True)
class MatchResult:
    client_ref: str
    status: str
    best: Candidate | None
    alternates: tuple[Candidate, ...]
    matcher_version: str | None
    retry_after_s: float | None


def _summary(value: object) -> CachedSummary | None:
    if not isinstance(value, dict):
        return None
    data = {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if not _SUMMARY_KEYS <= set(data) <= _SUMMARY_KEYS | {"has_downbeats", "has_events"}:
        return None
    version, bpm = text(data["analyzer_version"]), number(data["bpm"], 0.0)
    confidence, duration = number(data["bpm_confidence"], 0.0, 1.0), number(data["duration_s"], 0)
    flags = [data.get(f) for f in _SUMMARY_FLAGS]
    if version is None or bpm is None or bpm <= 0 or confidence is None or duration is None:
        return None
    if not isinstance(flags[0], bool) or any(
        f is not None and not isinstance(f, bool) for f in flags
    ):
        return None
    down, events = flags[1], flags[2]
    return CachedSummary(
        version,
        bpm,
        confidence,
        duration,
        flags[0],
        down if isinstance(down, bool) else None,
        events if isinstance(events, bool) else None,
    )


def _candidate(value: object) -> Candidate | None:
    if not isinstance(value, dict):
        return None
    data = {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    optional = {"cached_analysis_id", "cached_summary"}
    if not _CANDIDATE_KEYS <= set(data) <= _CANDIDATE_KEYS | optional:
        return None
    source = text(data["source"])
    confidence = number(data["recording_match_confidence"], 0.0, 1.0)
    band, check = data["band"], data["duration_check"]
    duration, delta = number(data["duration_s"], 0.0), number(data["duration_delta_s"])
    if source is None or ":" not in source or not source.split(":", 1)[0].isalpha():
        return None
    if confidence is None or duration is None or delta is None or check not in _CHECKS:
        return None
    if not isinstance(band, str) or band not in _BANDS:
        return None
    low, high = _BANDS[band]
    if not (low <= confidence < high or (band == "confident" and confidence == high)):
        return None
    cached = data.get("cached_analysis_id")
    summary = data.get("cached_summary")
    cached_id = None if cached is None else checked_id(cached)
    parsed_summary = None if summary is None else _summary(summary)
    if (cached is not None and cached_id is None) or (summary is not None and cached_id is None):
        return None
    if summary is not None and parsed_summary is None:
        return None
    return Candidate(
        source, confidence, band, duration, delta, str(check), cached_id, parsed_summary
    )


def parse_match_result(data: object) -> MatchResult | None:
    """One ``match-v1`` object."""
    if not isinstance(data, dict):
        return None
    item = {str(k): v for k, v in data.items()}  # pyright: ignore[reportUnknownVariableType]
    allowed = {"schema_version", "client_ref", "status", "alternates"}
    if not allowed <= set(item) <= allowed | {"best", "matcher_version", "retry_after_s"}:
        return None
    status, ref, alternates = item["status"], item["client_ref"], item["alternates"]
    if item["schema_version"] != "match-v1" or not isinstance(ref, str) or len(ref) > 128:
        return None
    if not isinstance(status, str) or status not in MATCH_STATUSES:
        return None
    if not isinstance(alternates, list) or len(alternates) > 2:  # pyright: ignore[reportUnknownArgumentType]
        return None
    parsed = tuple(_candidate(a) for a in alternates)  # pyright: ignore[reportUnknownVariableType]
    if any(c is None for c in parsed):
        return None
    with_best = status in ("matched", "uncertain")
    best = _candidate(item["best"]) if "best" in item else None
    version = item.get("matcher_version")
    if with_best != ("best" in item) or (with_best and (best is None or version is None)):
        return None
    if version is not None and text(version) is None:
        return None
    retry = item.get("retry_after_s")
    if (status == "rate_limited") != ("retry_after_s" in item):
        return None
    retry_after = None if retry is None else number(retry, 0.0)
    if retry is not None and retry_after is None:
        return None
    candidates = tuple(c for c in parsed if c is not None)
    return MatchResult(ref, status, best, candidates, text(version), retry_after)


def parse_match_answer(raw: bytes | None, refs: tuple[str, ...]) -> tuple[MatchResult, ...] | None:
    """``{"results": [match-v1, ...]}`` in request order: one per ``refs``, the same refs."""
    body = json_object(raw)
    if body is None or set(body) != {"results"} or not isinstance(body["results"], list):
        return None
    results = tuple(parse_match_result(r) for r in body["results"])  # pyright: ignore[reportUnknownVariableType]
    if len(results) != len(refs) or any(r is None for r in results):
        return None
    checked = tuple(r for r in results if r is not None)
    return checked if tuple(r.client_ref for r in checked) == refs else None
