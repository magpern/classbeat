"""``Provenance._lineage`` (ADR 0007 section 2, ADR 0038 section 4, #1186): a revise names its
base and its request and a design neither; a ``sketch`` may name both (a chat change of a sketch)
or neither (the composer's first design), never just one. Pure: no database."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.records import Provenance

BASE: dict[str, Any] = {
    "provider": "fake",
    "model": "fake-model",
    "model_answered": "fake-model",
    "effort": "low",
    "prompt_version": "0" * 64,
    "classbeat_version": "0.0.0",
    "mowl_version": "0.0.0",
    "map_version": None,
    "crossfade_sec": None,
    "agent_run_ids": [str(uuid.uuid4())],
    "usage": {
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    },
    "cost_micros": 1,
}
BASE_ID, REQUEST_ID = str(uuid.uuid4()), str(uuid.uuid4())
BOTH = {"base_revision_id": BASE_ID, "request_message_id": REQUEST_ID}
BASE_ONLY = {"base_revision_id": BASE_ID}
REQUEST_ONLY = {"request_message_id": REQUEST_ID}


def made(origin: str, lineage: dict[str, str]) -> Provenance:
    return Provenance.model_validate_json(json.dumps({**BASE, "origin": origin, **lineage}))


@pytest.mark.parametrize(
    ("origin", "lineage", "valid"),
    [
        ("sketch", {}, True),  # the composer's first design
        ("sketch", BOTH, True),  # a chat change of a sketch
        ("sketch", BASE_ONLY, False),
        ("sketch", REQUEST_ONLY, False),
        ("revise_draft", BOTH, True),
        ("revise_draft", {}, False),
        ("revise_draft", BASE_ONLY, False),
        ("design_draft", {}, True),
        ("design_draft", BOTH, False),
        ("design_with_songs", REQUEST_ONLY, False),
    ],
)
def test_the_lineage_rule(origin: str, lineage: dict[str, str], valid: bool) -> None:
    if valid:
        assert made(origin, lineage).origin == origin
    else:
        with pytest.raises(ValidationError, match="names its base and request"):
            made(origin, lineage)
