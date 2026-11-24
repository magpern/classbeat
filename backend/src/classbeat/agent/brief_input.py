"""The brief as the design and song stages see it (ADR 0006 section 7; pure), split out of
``model_input``.

An allowlist of the brief's fields: closed numbers and enums, the style and the instructor's text,
and, since #1485, the ``shape`` of the ride (closed sections, only when it is not empty). The
counts ``climbs``, ``sprints`` and ``races`` are sent only when they are more than 0 or the
instructor stated them (``Brief.explicit``): a count nobody gave is absent, never a zero that
contradicts the request's own words.
"""

from typing import Any, Final

from classbeat.agent.brief import Brief

COUNTS: Final = ("climbs", "sprints", "races")


def brief_for_model(brief: Brief) -> dict[str, Any]:
    out: dict[str, Any] = {"length_min": brief.length_min, "intensity": brief.intensity}
    for count in COUNTS:
        value: int = getattr(brief, count)
        if value > 0 or count in brief.explicit:
            out[count] = value
    out |= {"ftp_test": brief.ftp_test, "style": brief.style, "text": brief.text}
    if brief.shape:
        out["shape"] = brief.model_dump(mode="json")["shape"]
    return out
