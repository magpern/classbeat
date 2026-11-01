"""Pattern notes of a revision (ADR 0017 section 4), computed on read.

A pure function of the revision's content, the class's stored brief and the CURRENT profile:
nothing is stored per revision. The provenance digest only answers "did the model see other
rules than the ones shown now?", which sets a flag, never an error. No profile applies today
(or the stored brief is gone): no notes and no flag.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from classbeat.agent.schemas import Brief
from classbeat.design.rules import MAX_HITS, default_registry, evaluate
from classbeat.design.selection import select_profile_for_brief


@dataclass(frozen=True, slots=True)
class PatternNotes:
    hits: list[dict[str, Any]]
    uses_current_rules: bool


def _seen_digest(provenance: Mapping[str, Any]) -> str | None:
    rules = provenance.get("rules")
    digest = rules.get("digest") if isinstance(rules, dict) else None
    return digest if isinstance(digest, str) else None


def pattern_notes(
    spec: Mapping[str, Any],
    brief: Brief | None,
    dismissed: Collection[str],
    provenance: Mapping[str, Any],
) -> PatternNotes:
    """At most ``MAX_HITS`` visible hits of the current profile, dismissed rules dropped.

    ``uses_current_rules`` is set when the revision's recorded digest differs from the loaded
    profile's; a revision with no recorded digest sets nothing.
    """
    profile = None if brief is None else select_profile_for_brief(brief)
    if brief is None or profile is None:
        return PatternNotes([], False)
    found = evaluate(spec, brief, profile, frozenset(dismissed))[:MAX_HITS]
    hits = [
        {
            "rule": h.rule,
            "severity": h.severity,
            "metric": h.metric,
            "value": round(h.value, 3),
            "bounds": [h.low, h.high],
            "explained_by_brief": h.explained_by_brief,
        }
        for h in found
    ]
    seen = _seen_digest(provenance)
    current = default_registry().digests[profile.id]
    return PatternNotes(hits, seen is not None and seen != current)
