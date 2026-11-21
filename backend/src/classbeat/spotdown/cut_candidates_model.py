"""A cut candidate (``cut_candidates_calc``, #1328) and its stored shape. Pure: no I/O.

A candidate is a point on the played clock with its kind, its feature's ``confidence`` and
``method``, and ``trusted`` (inherited: ``cut_candidates_calc``). The studio stores a track's
candidates in its track map under ``MAP_KEY`` (``studio_map_model``); ``parse`` reads them back
and refuses the whole list (``None``) when any item is not one, so a stored list is used whole or
not at all.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown import feature_trust_policy as ft

MAP_KEY: Final = "cut_candidates"
KINDS: Final = frozenset({
    "content_start", "content_end", "silence_start", "silence_end", "fade_in_start",
    "fade_in_end", "fade_out_start", "fade_out_end", "segment_start", "label_start",
    "phrase_start", "phrase_end", "downbeat",
})  # fmt: skip
LABELS: Final = frozenset({"intro", "verse", "chorus", "bridge", "outro", "breakdown"})


@dataclass(frozen=True, slots=True)
class Candidate:
    at_s: float
    kind: str
    confidence: float | None
    method: str | None  # one of ``feature_trust_policy.METHODS``, else ``None``
    trusted: bool
    label: str | None = None  # a ``label_start``'s label, from ``LABELS`` only


def as_json(found: Sequence[Candidate]) -> list[dict[str, object]]:
    return [
        {"at_s": c.at_s, "kind": c.kind, "confidence": c.confidence, "method": c.method,
         "trusted": c.trusted, **({"label": c.label} if c.label else {})}
        for c in found
    ]  # fmt: skip


def _one(item: Mapping[str, object]) -> Candidate | None:
    at, kind, trusted = ft.num(item.get("at_s"), 0.0), item.get("kind"), item.get("trusted")
    method, label, raw = item.get("method"), item.get("label"), item.get("confidence")
    confidence = None if raw is None else ft.num(raw, 0.0, 1.0)
    if at is None or not isinstance(kind, str) or kind not in KINDS:
        return None
    if not isinstance(trusted, bool) or (raw is not None and confidence is None):
        return None
    if method is not None and (not isinstance(method, str) or method not in ft.METHODS):
        return None
    if label is not None and (not isinstance(label, str) or label not in LABELS):
        return None
    return Candidate(at, kind, confidence, method, trusted, label)


def parse(raw: object) -> tuple[Candidate, ...] | None:
    """Stored candidates read back; ``None`` for a non-list or when any item is not one."""
    if not isinstance(raw, list):
        return None
    out: list[Candidate] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        one = _one(item) if isinstance(item, Mapping) else None  # pyright: ignore[reportUnknownArgumentType]
        if one is None:
            return None
        out.append(one)
    return tuple(out)
