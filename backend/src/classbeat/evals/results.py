"""What a case run reports (ADR 0006 section 8, ADR 0012 section 4.2): ``CaseResult``, the
shape of the class and the results formats. Pure: no I/O. Split out of ``classbeat.evals.runner``,
which re-exports these names.

``CaseResult`` holds numbers, check names and details (numbers and rule names), the category and
the shape of the class (per segment its name and kind, per interval its length, zone and the index
of the track under it, none for a soundless class) only: no cues, no description, no song titles,
no ids. ``results_table`` and ``results_json`` are what the owner pastes into a PR;
``classbeat.evals.compare`` reads the JSON.

The suite writes **format 2**: ``suite_results`` is one file per run of the suite, the
``SuiteHeader`` (suite version and digest, the choice, the prompt version of each stage, the
classbeat, mowl and price versions, the repeat count, the times and the provider kind built), then
per run of a case the format-1 row plus its ``category`` (``suite_row``) or a song run's row
(``song_suite_row``). Same rule: numbers and names only. ``smoke --live`` keeps format 1.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from classbeat import __version__ as _classbeat_version
from classbeat.agent.prices import PRICES_VERSION
from classbeat.agent.provider import Usage
from classbeat.agent.records import mowl_version as _mowl_version
from classbeat.design.rules import default_registry
from classbeat.evals.checks import Check

if TYPE_CHECKING:
    from classbeat.evals.pick import PickCaseResult

RESULTS_FORMAT = 1  # of ``results_json`` (``smoke --live``); ``classbeat.evals.compare`` reads it
RESULTS_FORMAT_SUITE = 2  # of ``suite_results`` (ADR 0012 section 4.2); ``evals.gate`` reads it


@dataclass(frozen=True)
class CaseResult:
    name: str
    outcome: str  # "submitted" or the failure category
    checks: list[Check] = field(default_factory=lambda: list[Check]())
    turns: int = 0
    tool_calls: int = 0
    cost_micros: int = 0
    prompt_version: str = ""
    model_answered: str = ""
    duration_sec: int = 0
    tss: float = 0.0
    # What a comparison of models needs (issue #134); the zero values are a run that never got
    # that far.
    model: str = ""  # the model asked for (``model_answered`` is the one that answered)
    effort: str = ""
    repeat: int = 0  # 0-based index of this run of the case
    corrections: int = 0
    wall_sec: float = 0.0
    usage: Usage = field(default_factory=Usage)
    tss_bounds: tuple[float, float] | None = None
    changes_on_music: str = ""  # "x of y" interval starts on the music, as quality.report says
    timeline: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    tss_approximate: bool = False  # a zone 7 interval: its TSS weight is a guess (ADR 0012 s. 7)

    @property
    def passed(self) -> bool:
        return self.outcome == "submitted" and bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


def class_shape(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The timeline cut down to what is drawn: segment name and kind; per interval the seconds, the
    zone, whether standing and the index of the track under it (a running interval: its gradient
    instead of standing). No cues, no song titles."""
    return [
        {
            "name": str(seg["name"])[:80],
            "kind": str(seg["kind"]),
            "intervals": [_shape(i) for i in seg["intervals"]],
        }
        for seg in timeline
    ]


def _shape(i: dict[str, Any]) -> dict[str, Any]:
    if "standing" not in i:  # a running interval (``running_input.running_timeline_for_model``)
        return {
            "sec": int(i["sec"]),
            "zone": int(i["zone"]),
            "gradient": int(i["gradient"]),
            "track": int(i["track"]),
        }
    shape: dict[str, Any] = {
        "sec": int(i["sec"]),
        "zone": int(i["zone"]),
        "standing": bool(i["standing"]),
    }
    if "track" in i:  # a soundless interval (``soundless_input.timeline_for_model``) has no track
        shape["track"] = int(i["track"])
    return shape


def results_table(results: list[CaseResult]) -> str:
    lines = [
        "| case | result | outcome | turns | cost | prompt version | failed checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {'pass' if r.passed else 'FAIL'} | {r.outcome} | {r.turns} | "
            f"${r.cost_micros / 1e6:.4f} | {r.prompt_version[:12]} | "
            f"{', '.join(r.failed_checks) or '-'} |"
        )
    total = sum(r.cost_micros for r in results)
    passed = sum(r.passed for r in results)
    lines.append(f"\n{passed} of {len(results)} passed; total cost ${total / 1e6:.4f}")
    return "\n".join(lines)


def result_row(r: CaseResult) -> dict[str, Any]:
    return {
        "case": r.name,
        "repeat": r.repeat,
        "model": r.model,
        "effort": r.effort,
        "model_answered": r.model_answered,
        "prompt_version": r.prompt_version,
        "passed": r.passed,
        "outcome": r.outcome,
        "turns": r.turns,
        "tool_calls": r.tool_calls,
        "corrections": r.corrections,
        "cost_micros": r.cost_micros,
        "wall_sec": round(r.wall_sec, 2),
        "input_tokens": r.usage.input_tokens,
        "output_tokens": r.usage.output_tokens,
        "cache_read_tokens": r.usage.cache_read_tokens,
        "cache_write_tokens": r.usage.cache_write_tokens,
        "duration_sec": r.duration_sec,
        "tss": r.tss,
        "tss_bounds": list(r.tss_bounds) if r.tss_bounds else None,
        "changes_on_music": r.changes_on_music,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in r.checks],
        "timeline": r.timeline,
    }


def results_json(results: list[CaseResult]) -> str:
    """Format 1. ``rules_version`` is the one of the profiles the runs were made with, so
    ``compare`` can refuse to put runs on different rules side by side (ADR 0017 section 5)."""
    return json.dumps(
        {
            "format": RESULTS_FORMAT,
            "rules_version": default_registry().rules_version,
            "results": [result_row(r) for r in results],
        },
        indent=1,
    )


@dataclass(frozen=True)
class SuiteHeader:
    """The header of a suite results file (ADR 0012 section 4.2). ``provider``, ``model`` and
    ``effort`` are the catalog choice the run was given; ``provider_kind`` is the ``name`` of the
    provider actually built. Versions and times only: no key, no account, no model text."""

    suite_version: int
    manifest_digest: str
    provider: str
    model: str
    effort: str
    prompt_versions: Mapping[str, str]  # stage -> prompt_version
    repeat: int
    started_at: datetime
    ended_at: datetime
    provider_kind: str
    classbeat_version: str = _classbeat_version
    mowl_version: str = field(default_factory=_mowl_version)
    prices_version: str = PRICES_VERSION
    rules_version: int = field(default_factory=lambda: default_registry().rules_version)


def _utc(at: datetime) -> str:
    if at.tzinfo is None:
        raise ValueError("a results time is timezone-aware")
    return at.astimezone(UTC).isoformat()


def suite_row(r: CaseResult, category: str) -> dict[str, Any]:
    """A design or revise run in format 2: the format-1 row plus its category, the zone 7 flag
    and the scores (none for a design run)."""
    return {
        **result_row(r),
        "category": category,
        "tss_approximate": r.tss_approximate,
        "scores": {},
    }


def song_suite_row(
    r: "PickCaseResult", category: str, *, repeat: int, model: str, effort: str
) -> dict[str, Any]:
    """A song run (``pick_pool`` then ``choose_songs``) in format 2: numbers, check names and
    details (roles and seconds), the prompt version of each stage. No title, artist or id."""
    return {
        "case": r.name,
        "repeat": repeat,
        "category": category,
        "model": model,
        "effort": effort,
        "prompt_versions": dict(r.prompt_versions),
        "passed": r.passed,
        "outcome": r.outcome,
        "turns": r.turns,
        "tool_calls": r.tool_calls,
        "cost_micros": r.cost_micros,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in r.checks],
        "scores": dict(r.scores),
    }


def suite_results(header: SuiteHeader, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One file per run of the suite: the header of section 4.2, then the runs."""
    return {
        "results_format": RESULTS_FORMAT_SUITE,
        "suite_version": header.suite_version,
        "manifest_digest": header.manifest_digest,
        "provider": header.provider,
        "model": header.model,
        "effort": header.effort,
        "prompt_versions": dict(sorted(header.prompt_versions.items())),
        "classbeat_version": header.classbeat_version,
        "mowl_version": header.mowl_version,
        "prices_version": header.prices_version,
        "rules_version": header.rules_version,
        "repeat": header.repeat,
        "started_at": _utc(header.started_at),
        "ended_at": _utc(header.ended_at),
        "provider_kind": header.provider_kind,
        "results": [dict(row) for row in rows],
    }


def suite_results_json(header: SuiteHeader, rows: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(suite_results(header, rows), indent=1, allow_nan=False)


def failed_check_lines(r: CaseResult) -> list[str]:
    """One line per failed check with its detail (numbers and rule names only)."""
    return [f"  {r.name} failed {c.name}: {c.detail}" for c in r.checks if not c.ok]
