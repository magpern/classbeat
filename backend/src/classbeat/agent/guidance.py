"""What the agent layer needs from the soft rules (ADR 0017 section 3), as a protocol.

``classbeat.design`` imports the agent, so the agent cannot import the rules; the job kinds build
a ``classbeat.design.guidance.RuleSet`` and hand it to the stage context. Everything here is
optional: ``None`` means no profile applies, so the model input and the tool results are exactly
what they were before the rules existed.
"""

from collections.abc import Mapping
from typing import Any, Protocol


class Guidance(Protocol):
    def section(self) -> str:
        """The rules section of the first user message (at most 8 lines, ours only)."""
        ...

    def hits(self, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The violated rules of the fitted ``spec`` as tool-result entries (at most 5)."""
        ...

    def provenance(self) -> dict[str, Any]:
        """``{profile, version, digest}``: the profile the model saw."""
        ...


def rules_result(guidance: Guidance | None, spec: Mapping[str, Any]) -> dict[str, Any]:
    """``{"rules": [...]}`` for a tool result, or ``{}`` when no profile applies."""
    return {} if guidance is None else {"rules": guidance.hits(spec)}
