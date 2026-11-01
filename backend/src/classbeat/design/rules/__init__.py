"""Soft-rule profiles (ADR 0017): versioned, numbers-and-ids-only data files and their loader.

Pure code over repository data: nothing here imports the agent or the database.
"""

from classbeat.design.rules.engine import MAX_HITS, BriefLike, RuleHit, evaluate
from classbeat.design.rules.enums import SOURCE
from classbeat.design.rules.profile import (
    Profile,
    ProfileError,
    ProfileRule,
    Registry,
    default_registry,
    load_registry,
    select_profile,
)
from classbeat.design.rules.rationales import RATIONALES

__all__ = [
    "MAX_HITS",
    "RATIONALES",
    "SOURCE",
    "BriefLike",
    "Profile",
    "ProfileError",
    "ProfileRule",
    "Registry",
    "RuleHit",
    "default_registry",
    "evaluate",
    "load_registry",
    "select_profile",
]
