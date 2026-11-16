"""The provenance of a duplicate's revision 1 (ADR 0007 amendment of 2026-11-03, part D): the
source revision's descriptive fields only, ``origin: "duplicate"`` and ``duplicated_from``, no
run, usage or cost. Moved out of ``classbeat.classes.duplicate`` unchanged (SR C5b, the
repository's split-on-touch rule).
"""

from typing import Any, Final
from uuid import UUID

from classbeat.agent.records import Provenance
from classbeat.agent.stated_layer import StatedLayer

# The descriptive fields of the source revision's provenance: how the content was made. Run ids,
# usage, cost and the lineage of the source class are never copied.
DESCRIPTIVE: Final = (
    "provider",
    "model",
    "model_answered",
    "effort",
    "prompt_version",
    "classbeat_version",
    "mowl_version",
    "map_version",
    "crossfade_sec",
    "route_key",
    "eval_result_id",
    "tss_approximate",
    "rules",
    "sport",
)
ZERO_USAGE: Final = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownArgumentType]


def provenance_copy(
    source: Any,
    class_id: UUID,
    revision_id: UUID,
    number: int,
    *,
    idempotency_key: str,
    request_hash: str,
    stated: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The duplicate's provenance (``ValidationError`` when the source's no longer checks);
    ``stated`` is the source revision's nearest stated layer (#907), or ``None`` for none."""
    found = _dict(source)
    fields = {k: found[k] for k in DESCRIPTIVE if k in found}
    built = Provenance.model_validate(
        {
            **fields,
            "origin": "duplicate",
            "duplicated_from": {
                "class_id": str(class_id),
                "revision_id": str(revision_id),
                "number": number,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
            },
            **({} if stated is None else {"stated": stated}),
            "agent_run_ids": [],
            "usage": ZERO_USAGE,
            "cost_micros": 0,
        },
        strict=False,
    )
    out = built.model_dump(mode="json")
    if stated is not None:
        StatedLayer.model_validate(stated)  # strictly, before it is copied (ValidationError)
        out["stated"] = stated  # as the source stored it, not with defaults filled in
    return out
