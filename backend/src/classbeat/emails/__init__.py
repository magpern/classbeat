"""Account emails (ADR 0022): the registry, the block layout and the one send path."""

from classbeat.emails.dispatch import MailPrefs, branding_for, build, render, send
from classbeat.emails.registry import REGISTRY, Category, Kind

__all__ = ["REGISTRY", "Category", "Kind", "MailPrefs", "branding_for", "build", "render", "send"]
