"""The failure kinds of ADR 0040 section 8 (amendment "spotdown is the only analysis path", item 4)
as a table, shared by the studio-track and the mix job tests (#1353).

Each kind is ``(id, setup, category)``: ``setup`` is what the fake spotdown or the worker does
(``apply``) and ``category`` is the job category it must end in. The expected categories are
written out here by hand, not read from ``outage_policy``, so the table checks it.
"""

from typing import Any, get_args

import pytest
from _pytest.mark.structures import ParameterSet

from classbeat.spotdown import studio_policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import Unreachable, UnreachableReason
from spotdownfake import FakeSpotdown

UNAVAILABLE, FAILED, INTERNAL = "analysis_unavailable", "analysis_failed", "internal"
# Every UnreachableReason, with the category it ends the job in.
REASONS: dict[str, str] = {
    "not_configured": UNAVAILABLE,
    "source_off": UNAVAILABLE,
    "source_disabled": UNAVAILABLE,
    "blocked": UNAVAILABLE,
    "connection": UNAVAILABLE,
    "unauthorized": UNAVAILABLE,
    "server_error": UNAVAILABLE,
    "too_large": FAILED,
    "rejected": FAILED,
    "bad_answer": FAILED,
    "conflict": INTERNAL,
}
CONFIG_REASONS = ("not_configured", "source_off")  # no client call: the worker has no spotdown
STATUSES: dict[str, str] = {  # the fake's ``end``: a job status spotdown answers
    "processing_failed:decode_error": FAILED,
    "processing_failed:out_of_memory": FAILED,
    "processing_failed:analysis_error": FAILED,
    "processing_failed:timeout": FAILED,  # after its one retry
    "alignment_failed": FAILED,
    "source_unavailable": FAILED,
    "not_found": FAILED,
    "uncertain": FAILED,
    "provider_error": UNAVAILABLE,  # after its one retry
}
OTHERS = (
    ("rate_limited_over_budget", UNAVAILABLE),
    ("budget_spent", UNAVAILABLE),
    ("other_bytes", FAILED),  # a document of other bytes is refused
    ("unmappable_map", FAILED),  # the table check refuses the map
    ("error_in_a_step", INTERNAL),  # any other exception is not swallowed
)


def all_kinds() -> list[ParameterSet]:
    assert set(REASONS) == set(get_args(UnreachableReason))  # one row per reason, none forgotten
    rows: list[tuple[str, str]] = [(f"unreachable:{r}", c) for r, c in REASONS.items()]
    rows += [(f"end:{e}", c) for e, c in STATUSES.items()]
    rows += list(OTHERS)
    return [pytest.param(kind, category, id=kind) for kind, category in rows]


def apply(
    kind: str,
    fake: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmappable: tuple[Any, str, Any],
    broken: tuple[Any, str],
) -> str | None:
    """Make the fake (or the worker) fail as ``kind`` says. ``unmappable`` is ``(module, name,
    replacement)`` for the job's table check and ``broken`` is ``(module, name)`` of a step that is
    made to raise. Returns the reason a config kind needs the caller to install, else ``None``."""
    what, _, value = kind.partition(":")
    if what == "unreachable":
        if value in CONFIG_REASONS:
            return value
        reason: UnreachableReason = value  # pyright: ignore[reportAssignmentType]

        async def lookup(*_args: object, **_kwargs: object) -> Unreachable:
            return Unreachable(reason)

        monkeypatch.setattr(SpotdownClient, "lookup_content", lookup)
    elif what == "end":
        fake.end = value
    elif kind == "rate_limited_over_budget":
        monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.3)
        fake.end = "rate_limited"
    elif kind == "budget_spent":
        monkeypatch.setattr(studio_policy, "WAIT_SECONDS", 0.0)
    elif kind == "other_bytes":
        real = fake.analysis

        def other_bytes(analysis_id: str) -> dict[str, Any]:
            document = real(analysis_id)
            document["provenance"]["audio"]["sha256"] = "e" * 64
            return document

        monkeypatch.setattr(fake, "analysis", other_bytes)
    elif kind == "unmappable_map":
        module, name, refuse = unmappable
        monkeypatch.setattr(module, name, refuse)
    else:
        module, name = broken

        def raising(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("a bug")

        monkeypatch.setattr(module, name, raising)
    return None
