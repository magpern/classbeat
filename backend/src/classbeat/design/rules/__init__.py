"""Soft-rule profiles (ADR 0017): versioned, numbers-and-ids-only data files and their loader.

Pure code over repository data: nothing here imports the agent or the database.
"""

from classbeat.design.rules.enums import SOURCE
from classbeat.design.rules.profile import (
    Profile,
    ProfileError,
    ProfileRule,
    Registry,
    load_registry,
    select_profile,
)
from classbeat.design.rules.rationales import RATIONALES

__all__ = [
    "RATIONALES",
    "SOURCE",
    "Profile",
    "ProfileError",
    "ProfileRule",
    "Registry",
    "load_registry",
    "select_profile",
]
