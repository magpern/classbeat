"""Deterministic checks on the draft a design run submitted (ADR 0006 section 8).

``run_checks(case, result, outcome, limits, music_sec)`` returns one ``Check`` per rule; a case
passes when all of them do. They look only at the fitted spec the backend computed
(``DesignResult``), never at model text:

- ``structure``: a warm-up first, at least one main segment;
- ``validate``: ``workout.validate()`` passes on the fitted spec;
- ``duration``: within 5 % of the asked length and not longer than the music (or, for music
  shorter than the ask, 90 % to 100 % of the music);
- ``cooldown``: exactly one cooldown, last, of at least 60 s;
- ``sprints_and_races`` / ``climbs`` / ``ftp_test``: what the brief asked for is present (and no
  FTP test when none was asked). A burst is a seated interval in zone 6 or 7 of at most 45 s; a
  race has the shape the prompt gives (15 to 35 s); a climb is a run of at least 90 s in zone 3 or
  above whose mean cadence stays at or under 75 rpm, or that sits in a segment named "climb";
- ``zones``: the share of the time in zone 5 or above within the bounds of the asked intensity;
- ``tss``: the backend's TSS within the bounds of the intensity and the length;
- ``changes_on_music``, ``quality_issues``: from ``mowl.quality.report`` (computed by
  ``model_input.quality_for_model``, not re-implemented here): the share of interval starts that
  landed on a drop, breakdown, track change or bar start, and no standing, cadence or zone-run
  issue left in the submitted draft;
- ``forbidden_text``: none of the case's strings (injection text) in what the draft says;
- ``limits``: turns, tool calls and cost within the run's limits and the case's own bounds.

A check's detail holds numbers and rule names only, never draft or music text.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl import workout

from classbeat.agent.design import DesignResult
from classbeat.agent.limits import Limits
from classbeat.agent.loop import StageOutcome
from classbeat.design.revision import canonical, fitted_draft
from classbeat.evals.cases import Case, Range

DURATION_TOLERANCE: Final = 0.05
MUSIC_FILL: Final = 0.9
BURST_MAX_SEC: Final = 45
RACE_SEC: Final = (15, 35)
CLIMB_MIN_SEC: Final = 90
CLIMB_MAX_RPM: Final = 75
HARD_ZONE: Final = 5
# Share of the time in zone 5 or above, per intensity ("about a tenth" in a typical class).
HARD_SHARE: Final[dict[str, Range]] = {
    "easy": (0.0, 0.10),
    "moderate": (0.03, 0.25),
    "hard": (0.10, 0.40),
}
# TSS per hour of class, per intensity (an hour at threshold is 100).
TSS_PER_HOUR: Final[dict[str, Range]] = {
    "easy": (25.0, 55.0),
    "moderate": (40.0, 75.0),
    "hard": (55.0, 100.0),
}
# beat_off: a fallback cadence, not a mistake. loudness_mismatch: the smoke music's loudness is a
# fixed cycle (evals.music.LOUDNESS) unrelated to any design, so here it is a warning to the model,
# not a measure of the design.
TOLERATED_ISSUES: Final = frozenset({"beat_off", "loudness_mismatch"})


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _intervals(spec: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(seg, i) for seg in spec["segments"] for i in seg["intervals"]]


def _bursts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        i
        for _, i in _intervals(spec)
        if not i.get("standing")
        and i["zone"] >= 6
        and i["sec"] <= BURST_MAX_SEC
        and "ftp_test" not in i
    ]


MAX_NOTE: Final = 300  # characters, as the tool allows


def _climbs(spec: dict[str, Any]) -> int:
    count, run = 0, 0
    for seg, i in _intervals(spec):
        rpm = i.get("rpm")
        slow = isinstance(rpm, list) and 0 < (rpm[0] + rpm[1]) / 2 <= CLIMB_MAX_RPM
        named = "climb" in str(seg["name"]).lower()
        if i["zone"] >= 3 and (slow or named) and "ftp_test" not in i:
            run += i["sec"]
            continue
        count += run >= CLIMB_MIN_SEC
        run = 0
    return count + (run >= CLIMB_MIN_SEC)


def _clock(text: str) -> int:
    minutes, _, seconds = text.partition(":")
    return int(minutes) * 60 + int(seconds)


def _run_holds_ftp_test(spec: dict[str, Any], quality: dict[str, Any]) -> bool:
    """Whether the longest zone run ``quality.report`` names is the one with the FTP test in it
    (the report names only the longest run, so a second long run elsewhere is not seen)."""
    run = quality["longest_zone_run"]
    start = _clock(str(run["from"]))
    t = 0
    for _, i in _intervals(spec):
        if "ftp_test" in i:
            return i["zone"] == run["zone"] and start <= t < start + int(run["sec"])
        t += i["sec"]
    return False


def _texts(result: DesignResult, note: str = "") -> list[str]:
    draft = result.draft
    out = [draft.name, draft.description, note]
    for seg in draft.segments:
        out.append(seg.name)
        out.extend(i.cue for i in seg.intervals)
    return out


def _between(value: float, bounds: Range) -> bool:
    return bounds[0] <= value <= bounds[1]


def tss_bounds_for(case: Case, total_sec: float) -> Range:
    """The TSS the draft must land in: the case's own, else the intensity's per hour of class."""
    per_hour = TSS_PER_HOUR[case.brief.intensity]
    return case.expect.tss or (per_hour[0] * total_sec / 3600, per_hour[1] * total_sec / 3600)


def run_checks(
    case: Case,
    result: DesignResult,
    outcome: StageOutcome,
    limits: Limits,
    music_sec: float,
    note: str = "",
) -> list[Check]:
    spec, brief, expect = result.spec, case.brief, case.expect
    total = result.duration_sec
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name, bool(ok), detail))

    segs = spec["segments"]
    first, mains = segs[0], [s for s in segs if s["kind"] == "main"]
    add(
        "structure",
        first["kind"] == "warmup" and workout.seconds(first) >= 60 and bool(mains),
        f"{len(segs)} segments, {len(mains)} main, first is {first['kind']}",
    )
    try:
        workout.validate(spec)
        add("validate", True, "the fitted spec passes workout.validate")
    except ValueError:
        add("validate", False, "the fitted spec fails workout.validate")

    asked = brief.length_min * 60
    if expect.duration == "asked":
        ok = abs(total - asked) <= DURATION_TOLERANCE * asked and total <= music_sec + 1
        add("duration", ok, f"{total} s for {asked} s asked, music {math.floor(music_sec)} s")
    else:
        ok = MUSIC_FILL * music_sec <= total <= music_sec + 1 and total <= asked
        add("duration", ok, f"{total} s of music {math.floor(music_sec)} s ({asked} s asked)")

    cooldowns = [s for s in segs if s["kind"] == "cooldown"]
    add(
        "cooldown",
        len(cooldowns) == 1
        and segs[-1]["kind"] == "cooldown"
        and workout.seconds(segs[-1]) >= workout.MIN_COOLDOWN_SEC,
        f"{len(cooldowns)} cooldown, last {workout.seconds(segs[-1])} s",
    )

    bursts = _bursts(spec)
    races = [i for i in bursts if RACE_SEC[0] <= i["sec"] <= RACE_SEC[1]]
    add(
        "sprints_and_races",
        len(bursts) >= brief.sprints + brief.races and len(races) >= brief.races,
        f"{len(bursts)} bursts and {len(races)} race-shaped for "
        f"{brief.sprints} sprints and {brief.races} races asked",
    )
    climbs = _climbs(spec)
    add("climbs", climbs >= brief.climbs, f"{climbs} climbs for {brief.climbs} asked")

    ftp = [i["ftp_test"] for _, i in _intervals(spec) if "ftp_test" in i]
    add(
        "ftp_test",
        ftp == ([brief.ftp_test] if brief.ftp_test else []),
        f"FTP tests {ftp} for {brief.ftp_test} asked",
    )

    hard = sum(i["sec"] for _, i in _intervals(spec) if i["zone"] >= HARD_ZONE)
    share = hard / total if total else 0.0
    bounds = expect.hard_share or HARD_SHARE[brief.intensity]
    add("zones", _between(share, bounds), f"zone {HARD_ZONE}+ share {share:.2f}, bounds {bounds}")

    tss_bounds = tss_bounds_for(case, total)
    add(
        "tss",
        _between(result.tss, tss_bounds),
        f"TSS {result.tss}, bounds {tss_bounds[0]:.0f} to {tss_bounds[1]:.0f}",
    )

    moved = str(result.quality.get("changes_on_music", "0 of 0"))
    on, _, of = moved.partition(" of ")
    ratio = int(on) / int(of) if int(of) else 1.0
    add(
        "changes_on_music",
        ratio >= expect.changes_on_music_min,
        f"{moved} interval starts on the music, at least {expect.changes_on_music_min:.2f}",
    )
    allowed = TOLERATED_ISSUES | set(expect.allow_issues)
    codes = {str(i["code"]) for i in result.quality["issues"]} - allowed
    if "zone_run_too_long" in codes and _run_holds_ftp_test(spec, result.quality):
        codes.discard("zone_run_too_long")  # a 20 minute test is one long run by definition
    codes = sorted(codes)
    add("quality_issues", not codes, f"issues left: {codes}")

    if case.revise is not None:
        content = fitted_draft(result).model_dump(mode="json", exclude_none=True)
        base = case.revise.base.model_dump(mode="json", exclude_none=True)
        add(
            "changed",
            canonical(content) != canonical(base),
            "the revised draft differs from the base draft",
        )
        add("note", len(note) <= MAX_NOTE, f"a note of {len(note)} characters")

    lowered = [t.lower() for t in _texts(result, note)]
    leaked = [
        n for n, bad in enumerate(expect.forbidden_text) if any(bad.lower() in t for t in lowered)
    ]
    add("forbidden_text", not leaked, f"{len(leaked)} of {len(expect.forbidden_text)} found")

    max_turns = min(limits.max_turns, expect.max_turns or limits.max_turns)
    max_cost = min(limits.job_cost_micros, expect.max_cost_micros or limits.job_cost_micros)
    add(
        "limits",
        outcome.turns <= max_turns
        and outcome.tool_calls <= limits.max_tool_calls
        and outcome.cost_micros <= max_cost,
        f"{outcome.turns} turns of {max_turns}, {outcome.tool_calls} tool calls of "
        f"{limits.max_tool_calls}, cost {outcome.cost_micros} of {max_cost} micro-dollars",
    )
    return checks


def failed(checks: Sequence[Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]
