"""The shape checks of the design cases. Pure.

``follows_shape`` (#1493): the draft follows the brief's ride shape (``agent.shape_check``: every
section realised in order, its counts inside the range); the detail holds section paths and codes
only. The ``kept_shape`` revision check (ADR 0038 section 5, #1188): the composer's design on the
songs keeps the sketch's shape. Pure.

The revised class has the base's segments in the same order (each segment's ``kind``) and the
same number of main segments: the songs re-time the intervals, they do not reorder or drop parts.
The other revision checks (``kept_length``, ``kept_cooldown``, ``kept_efforts``) still apply.
"""

from typing import Any

from classbeat.agent.brief import Brief
from classbeat.agent.schemas import AnyDraft
from classbeat.agent.shape_check import check_shape


def kept_shape(spec: dict[str, Any], base: dict[str, Any]) -> tuple[bool, str]:
    now = [str(s["kind"]) for s in spec["segments"]]
    was = [str(s["kind"]) for s in base["segments"]]
    return now == was, f"segments {now}, the base {was}"


def follows_shape(brief: Brief, draft: AnyDraft, spec: dict[str, Any]) -> tuple[bool, str]:
    tags = [getattr(segment, "section", None) for segment in draft.segments]
    missed = [f"{m.path} {m.code}" for m in check_shape(brief, spec, tags)]
    return not missed, f"{len(brief.shape)} sections, missed: {missed}"
