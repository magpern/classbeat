"""The rule profile of a brief of either sport (ADR 0017 sections 2 and 7, ADR 0018; M8 R1a).

``select_profile_for`` and ``rules_for`` are what every job kind and the class page use:

- a cycling brief goes to ``design.selection.select_profile_for_brief`` and
  ``design.guidance.rule_set_for`` unchanged, so a cycling class gets exactly the rules it got
  before (those modules are eval-suite rule sources and are not touched by R1a);
- a running brief gets only a ``running`` profile (``select_profile(..., "running")``: a sport
  never borrows another's profile), by the same class type rule
  (``selection.resolve_class_type``: the brief's own type, else ``interval`` for a hard class,
  else ``plain``; a running brief has no sprints, races or FTP test). Weak cells ship no file, so
  they give ``None``: no section, no hits, no provenance, no notes. Running rules are ``info``
  only (the runbook's ``moderate`` evidence); a running profile with any other severity applies
  nothing (fail closed).

The eval suite designs cycling classes only, so these running paths are outside its digest until
E1 adds running cases; E1 lists this module in ``evals.suite.RULE_SOURCES`` with them
(``tests/test_sport_rules.py`` pins that it is not yet listed, so the bump is a decision).
"""

from classbeat.agent.schemas import Brief
from classbeat.design.guidance import RuleSet, rule_set_for
from classbeat.design.rules import Profile, default_registry, select_profile
from classbeat.design.selection import resolve_class_type, select_profile_for_brief

RUNNING = "running"


def _running_profile(brief: Brief) -> Profile | None:
    profile = select_profile(resolve_class_type(brief).class_type, brief.length_min, RUNNING)
    if profile is None or profile.sport != RUNNING:
        return None
    if any(rule.severity != "info" for rule in profile.rules):
        return None
    return profile


def select_profile_for(brief: Brief) -> Profile | None:
    """The profile of ``brief``'s sport, or ``None`` (no profile is never an error)."""
    if brief.sport == RUNNING:
        return _running_profile(brief)
    return select_profile_for_brief(brief)


def rules_for(brief: Brief, dismissed: tuple[str, ...] | list[str] = ()) -> RuleSet | None:
    """``guidance.rule_set_for`` of ``brief``'s sport."""
    if brief.sport != RUNNING:
        return rule_set_for(brief, dismissed)
    profile = _running_profile(brief)
    if profile is None:
        return None
    rules = RuleSet(profile, default_registry().digests[profile.id], brief, frozenset(dismissed))
    return rules if rules.section() else None
