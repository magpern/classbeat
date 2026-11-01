"""The soft rules as the designer receives them (ADR 0017 section 3): ``RuleSet``.

Built by the job kinds from the stored brief and the class's dismissed rule ids, and handed to the
agent's stage context. It makes the section of the first user message, the ``rules`` entries of
the ``preview_draft`` and ``submit_draft`` results, and the ``rules`` of a revision's provenance.

Everything said to the model is ours: ids and numbers from the profile, sentences from
``RATIONALES``. No brief text, music text or profile free text (a profile has none) is used.
Dismissed rules are left out of the section and of the hits. Only the stored brief is read, never
a revise request, and a derived class type never counts as explained.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from classbeat.agent.schemas import Brief
from classbeat.design.rules import (
    RATIONALES,
    Profile,
    ProfileRule,
    RuleHit,
    default_registry,
    evaluate,
)
from classbeat.design.selection import select_profile_for_brief

MAX_SECTION_LINES: Final = 8
MAX_SECTION_CHARS: Final = 1200
HEADER: Final = "Patterns for this kind of class:"
STANDING: Final = (
    "These are patterns from professional classes, not requirements. Follow the instructor's "
    "brief first; deviate on purpose when the brief or the music asks for it, and keep the "
    "draft valid."
)
USUALLY: Final = "usually; the brief wins"
_RULE_LINES: Final = MAX_SECTION_LINES - 2  # the header and the standing sentence
_SECONDS: Final = ("first_zone4plus_s", "cooldown_s")
_SHARES: Final = (
    "zone4plus_time_share",
    "zone5plus_time_share",
    "zone4plus_share_in_first_25pct",
    "standing_time_share",
)


def _number(metric: str, value: float) -> str:
    if metric in _SECONDS:
        return f"{int(value) // 60}:{int(value) % 60:02d}"
    if metric in _SHARES:
        return f"{round(value * 100)}%"
    return f"{value:g}"


def _bound(rule: ProfileRule) -> str:
    if rule.low is not None:
        return f"at least {_number(rule.metric, rule.low)}"
    if rule.high is not None:
        return f"at most {_number(rule.metric, rule.high)}"
    return ""  # a validated rule has one bound; unreachable


def _line(rule: ProfileRule) -> str:
    sentence = RATIONALES[rule.rationale_id].rstrip(".")
    return f"- {sentence} ({_bound(rule)}; {USUALLY})."


def hit_entry(hit: RuleHit) -> dict[str, Any]:
    """One tool-result entry: ids, numbers and one sentence of ours."""
    return {
        "rule": hit.rule,
        "severity": hit.severity,
        "value": round(hit.value, 3),
        "bounds": [hit.low, hit.high],
        "explained_by_brief": hit.explained_by_brief,
        "note": RATIONALES[hit.rationale_id],
    }


@dataclass(frozen=True)
class RuleSet:
    profile: Profile
    digest: str
    brief: Brief
    dismissed: frozenset[str] = frozenset()

    def section(self) -> str:
        """The whole section is at most 8 lines: the header, the first 6 rules in the profile's
        order that are not dismissed, and the standing sentence. A profile may have more rules
        (13 or 14 today): ``hits`` covers all of them, so the model can get a hit for a pattern
        the section did not state. The profile's rule order is its priority order."""
        rules = [r for r in self.profile.rules if r.rule not in self.dismissed][:_RULE_LINES]
        if not rules:
            return ""
        return "\n".join([HEADER, *(_line(r) for r in rules), STANDING])

    def hits(self, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        found = evaluate(spec, self.brief, self.profile, self.dismissed)
        return [hit_entry(h) for h in found]

    def provenance(self) -> dict[str, Any]:
        return {"profile": self.profile.id, "version": self.profile.version, "digest": self.digest}


def rule_set_for(brief: Brief, dismissed: tuple[str, ...] | list[str] = ()) -> RuleSet | None:
    """The rules for ``brief``, or ``None`` (no profile: no section, no hits, no provenance)."""
    profile = select_profile_for_brief(brief)
    if profile is None:
        return None
    digest = default_registry().digests[profile.id]
    rules = RuleSet(profile, digest, brief, frozenset(dismissed))
    return rules if rules.section() else None  # every rule dismissed: nothing was shown
