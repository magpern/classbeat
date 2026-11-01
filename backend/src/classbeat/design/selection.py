"""The rule profile of a brief (ADR 0017 section 2): the class type, derived when the brief has
none, and ``select_profile_for_brief``.

Pure: no database, no model. A derived type is reported as derived and never counts as explained
by the brief (ADR 0017 section 1); only a ``class_type`` the user set does. The derivation never
invents ``hiit`` or ``journey``: those are chosen by the user or not at all.
"""

from dataclasses import dataclass
from typing import Literal

from classbeat.agent.schemas import Brief
from classbeat.design.rules import Profile, select_profile


@dataclass(frozen=True, slots=True)
class ClassTypeChoice:
    class_type: str
    source: Literal["brief", "derived"]

    @property
    def derived(self) -> bool:
        return self.source == "derived"


def resolve_class_type(brief: Brief) -> ClassTypeChoice:
    """The brief's own ``class_type``; else ``ftp_test`` set -> ``ftp_test``; ``sprints`` or
    ``races`` > 0 or intensity ``hard`` -> ``interval``; otherwise ``plain``."""
    if brief.class_type is not None:
        return ClassTypeChoice(brief.class_type, "brief")
    if brief.ftp_test is not None:
        return ClassTypeChoice("ftp_test", "derived")
    if brief.sprints > 0 or brief.races > 0 or brief.intensity == "hard":
        return ClassTypeChoice("interval", "derived")
    return ClassTypeChoice("plain", "derived")


def select_profile_for_brief(brief: Brief) -> Profile | None:
    """The profile for ``brief`` (cycling), or ``None``: no profile is never an error."""
    return select_profile(resolve_class_type(brief).class_type, brief.length_min)
