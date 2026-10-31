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
- ``tss``: the backend's TSS within the bounds of the intensity and the length (for a class
  with a zone 7 interval the upper bound widened by the suite's ``zone7_tss_margin``, ADR 0012
  section 7: zone 7's TSS weight is a guess);
- ``changes_on_music``, ``quality_issues``: from ``mowl.quality.report`` (computed by
  ``model_input.quality_for_model``, not re-implemented here): the share of interval starts that
  landed on a drop, breakdown, track change or bar start, and no standing, cadence or zone-run
  issue left in the submitted draft;
- ``forbidden_text``: none of the case's strings (injection text) in what the draft says;
- ``limits``: turns, tool calls and cost within the run's limits and the case's own bounds.

The suite's checks (ADR 0012 section 4), run only for a case whose ``expect`` asks for them:

- ``games``: each game the case lists exactly once, and no other (``workout.validate`` already
  holds the library's rules for them);
- ``ftp_warmup``: the FTP test starts at least ``ftp_warmup_min_sec`` into the class;
- ``sprints_on_drops``: every sprint (a burst, as above) starts within one bar of a drop, when
  the music has a drop;
- ``phrase_changes``: no zone change more than 1 s off a phrase boundary, drop, breakdown or
  track change when one is within 8 s (phrases from ``mowl.music.phrases``, events from the map);
- ``revision``, ``kept_length``, ``kept_cooldown``, ``kept_efforts``: the asked change
  (``RevisionExpect``) is made, and what it did not touch is kept, measured against the base.

A check's detail holds numbers and rule names only, never draft or music text.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl import music, workout

from classbeat.agent.design import DesignResult
from classbeat.agent.limits import Limits
from classbeat.agent.loop import StageOutcome
from classbeat.design.revision import canonical, fitted_draft
from classbeat.evals.cases import Case, Range, RevisionExpect

DURATION_TOLERANCE: Final = 0.05
MUSIC_FILL: Final = 0.9
BURST_MAX_SEC: Final = 45
RACE_SEC: Final = (15, 35)
CLIMB_MIN_SEC: Final = 90
CLIMB_MAX_RPM: Final = 75
HARD_ZONE: Final = 5
ZONE7: Final = 7
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
# The suite's music sync (ADR 0012 section 4): a change this close to a boundary is on it (fitted
# starts are whole seconds); a boundary this close makes a change mid-phrase avoidable.
ON_BOUNDARY_SEC: Final = 1.0
PHRASE_WINDOW_SEC: Final = 8.0
BEATS_PER_BAR: Final = 4
# Revisions: easier is at most this share of the base's TSS; the cooldown keeps within this.
EASIER_TSS: Final = 0.95
COOLDOWN_KEEP_SEC: Final = 60


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _intervals(spec: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(seg, i) for seg in spec["segments"] for i in seg["intervals"]]


def _bursts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for _, i in _intervals(spec) if _is_burst(i)]


def _is_burst(i: dict[str, Any]) -> bool:
    return (
        not i.get("standing")
        and i["zone"] >= 6
        and i["sec"] <= BURST_MAX_SEC
        and "ftp_test" not in i
    )


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


def has_zone7(spec: dict[str, Any]) -> bool:
    """Whether the class has a zone 7 interval, whose TSS weight is a guess (ADR 0012 section 7)."""
    return any(i["zone"] == ZONE7 for _, i in _intervals(spec))


def tss_bounds_for(
    case: Case, total_sec: float, *, zone7: bool = False, zone7_margin: float = 0.0
) -> Range:
    """The TSS the draft must land in: the case's own, else the intensity's per hour of class.
    For a class with zone 7 the upper bound is widened by ``zone7_margin`` (the suite's
    manifest sets it; the smoke set leaves it at 0)."""
    per_hour = TSS_PER_HOUR[case.brief.intensity]
    low, high = case.expect.tss or (per_hour[0] * total_sec / 3600, per_hour[1] * total_sec / 3600)
    return (low, high * (1 + zone7_margin)) if zone7 else (low, high)


def run_checks(
    case: Case,
    result: DesignResult,
    outcome: StageOutcome,
    limits: Limits,
    music_sec: float,
    note: str = "",
    *,
    zone7_tss_margin: float = 0.0,
    music_map: dict[str, Any] | None = None,
) -> list[Check]:
    """``music_map`` is the map the stage was given; the ``music_sync`` checks need it."""
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
    if expect.revision is not None and expect.revision.length_min is not None:
        asked = expect.revision.length_min * 60  # the request asked for another length
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

    tss_bounds = tss_bounds_for(case, total, zone7=has_zone7(spec), zone7_margin=zone7_tss_margin)
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

    checks.extend(suite_checks(case, result, music_map))

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


# --- the suite's checks (ADR 0012 section 4) ---------------------------------------------------


def _timed(spec: dict[str, Any]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    """(start in seconds, segment, interval) of every interval of a (fitted) spec."""
    out: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    t = 0
    for seg, i in _intervals(spec):
        out.append((t, seg, i))
        t += i["sec"]
    return out


def _bar_sec(music_map: dict[str, Any], t: float) -> float:
    return BEATS_PER_BAR * 60.0 / float(music.song_at(music_map, t)["bpm"])


def sprints_on_drops(spec: dict[str, Any], music_map: dict[str, Any]) -> Check:
    drops = [float(e["t"]) for e in music_map["events"] if e["kind"] == "drop"]
    starts = [t for t, _, i in _timed(spec) if _is_burst(i)]
    if not drops:
        return Check("sprints_on_drops", True, f"{len(starts)} sprints, no drop in the music")
    on = [t for t in starts if any(abs(t - d) <= _bar_sec(music_map, d) for d in drops)]
    return Check(
        "sprints_on_drops",
        len(on) == len(starts),
        f"{len(on)} of {len(starts)} sprints start within a bar of a drop",
    )


def phrase_changes(spec: dict[str, Any], music_map: dict[str, Any], allowed: int) -> Check:
    bounds = [*music.phrases(music_map), *(float(e["t"]) for e in music_map["events"])]
    timed = _timed(spec)
    changes = [
        t
        for (t, _, i), (_, _, before) in zip(timed[1:], timed[:-1], strict=True)
        if i["zone"] != before["zone"]
    ]
    off = 0
    for t in changes:
        near = min((abs(t - b) for b in bounds), default=math.inf)
        off += ON_BOUNDARY_SEC < near <= PHRASE_WINDOW_SEC
    return Check(
        "phrase_changes",
        off <= allowed,
        f"{off} of {len(changes)} zone changes mid-phrase, at most {allowed}",
    )


def _hard_share(spec: dict[str, Any]) -> float:
    total = sum(i["sec"] for _, i in _intervals(spec))
    hard = sum(i["sec"] for _, i in _intervals(spec) if i["zone"] >= HARD_ZONE)
    return hard / total if total else 0.0


def _is_climb(seg: dict[str, Any]) -> bool:
    return "climb" in str(seg["name"]).lower() or _climbs({"segments": [seg]}) > 0


def _efforts(spec: dict[str, Any]) -> tuple[int, int, int, int]:
    """Climbs, races, FTP tests and sprints (bursts that are not races) of a spec."""
    bursts = _bursts(spec)
    races = sum(RACE_SEC[0] <= i["sec"] <= RACE_SEC[1] and i["zone"] == ZONE7 for i in bursts)
    ftp = sum("ftp_test" in i for _, i in _intervals(spec))
    return _climbs(spec), races, ftp, len(bursts) - races


def _mains(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in spec["segments"] if s["kind"] == "main"]


def _predicate(
    want: RevisionExpect, spec: dict[str, Any], base: dict[str, Any], tss: float
) -> tuple[bool, str]:
    if want.kind == "more_sprints":
        now, before = len(_bursts(spec)), len(_bursts(base))
        return now >= before + want.by, f"{now} sprints, {before} before, {want.by} more asked"
    if want.kind == "easier":
        before, share, base_share = workout.tss(base), _hard_share(spec), _hard_share(base)
        ok = tss <= EASIER_TSS * before and share <= base_share
        return (
            ok,
            f"TSS {tss} from {before}, zone {HARD_ZONE}+ share {share:.2f} from {base_share:.2f}",
        )
    if want.kind == "shorter":
        asked = (want.length_min or 0) * 60
        total = workout.total_seconds(spec)
        return abs(total - asked) <= DURATION_TOLERANCE * asked, f"{total} s for {asked} s asked"
    mains = _mains(spec)
    at = mains[0] if want.climb_at == "start" else mains[-1]
    return bool(mains) and _is_climb(
        at
    ), f"the {want.climb_at} main segment is a climb: {_is_climb(at)}"


def revision_checks(
    want: RevisionExpect, result: DesignResult, base: dict[str, Any]
) -> list[Check]:
    spec = result.spec
    ok, detail = _predicate(want, spec, base, result.tss)
    out = [Check("revision", ok, f"{want.kind}: {detail}")]
    total, before = workout.total_seconds(spec), workout.total_seconds(base)
    if want.kind != "shorter":
        out.append(
            Check(
                "kept_length",
                abs(total - before) <= DURATION_TOLERANCE * before,
                f"{total} s, the base {before} s",
            )
        )
    cool, base_cool = spec["segments"][-1], base["segments"][-1]
    cool_sec, base_sec = workout.seconds(cool), workout.seconds(base_cool)
    out.append(
        Check(
            "kept_cooldown",
            cool["kind"] == "cooldown"
            and cool_sec >= workout.MIN_COOLDOWN_SEC
            and abs(cool_sec - base_sec) <= COOLDOWN_KEEP_SEC,
            f"cooldown {cool_sec} s, the base {base_sec} s",
        )
    )
    if want.kind in ("more_sprints", "climb_moved"):
        now, was = _efforts(spec), _efforts(base)
        if want.kind == "more_sprints":  # the sprints are what changed
            now, was = now[:-1], was[:-1]
        out.append(
            Check(
                "kept_efforts",
                now == was,
                f"climbs, races, FTP tests{'' if want.kind == 'more_sprints' else ', sprints'} "
                f"{list(now)}, the base {list(was)}",
            )
        )
    return out


def suite_checks(case: Case, result: DesignResult, music_map: dict[str, Any] | None) -> list[Check]:
    """The checks a suite case's ``expect`` asks for; none for a smoke case."""
    spec, expect = result.spec, case.expect
    out: list[Check] = []
    if expect.games:
        found = sorted(str(i["game"]) for _, i in _intervals(spec) if i.get("game"))
        out.append(
            Check(
                "games", found == sorted(expect.games), f"games {found} for {sorted(expect.games)}"
            )
        )
    if expect.ftp_warmup_min_sec is not None:
        starts = [t for t, _, i in _timed(spec) if "ftp_test" in i]
        out.append(
            Check(
                "ftp_warmup",
                len(starts) == 1 and starts[0] >= expect.ftp_warmup_min_sec,
                f"FTP test at {starts} s, at least {expect.ftp_warmup_min_sec} s",
            )
        )
    sync = expect.music_sync
    if sync is not None:
        if music_map is None:
            raise ValueError("the music_sync checks need the music map")
        if sync.sprints_on_drops:
            out.append(sprints_on_drops(spec, music_map))
        if sync.phrase_changes:
            out.append(phrase_changes(spec, music_map, sync.max_off_phrase))
    if expect.revision is not None and case.revise is not None:
        out += revision_checks(expect.revision, result, case.revise.base.to_spec())
    return out
