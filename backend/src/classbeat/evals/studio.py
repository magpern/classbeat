"""The SwedeBeat studio's smoke case (ADR 0036 section 4.4, SR C5): a class designed with its mix
through the real studio stage (``agent.studio_design``) on synthetic track analyses, with a
recorded ``FakeProvider`` script in CI. No model, no network, no database.

``load_studio_cases`` reads the cases of ``smoke_studio/`` (the design cases' own ``Case`` shape;
``music.tracks`` is how many tracks the approved list has). ``run_studio_case`` builds the tracks
(``studio_tracks``: one tempo each, a downbeat grid, a drop every 16 bars, a quiet and a loud
half), runs the stage on an in-memory host and checks the submitted draft with the design checks
(``checks.run_checks``, on the composed map) and the studio's own (``studio_checks``): the mix
plays every track at least once and no other (a repeat is allowed, #1342), each loop run (one
piece, or a loop's pieces together) plays at least 60 s, the total is the backend's one duration
rule and the composed map's, and no stand-in for a catalogue reference reached the model.

The local tracks have no stored timing decision and so no cut candidates (#1328); the spotdown
variant (``tests/test_evals_studio_spotdown.py``) gives the same tracks with their candidates, and
the recorded script's trimmed piece carries its ``why`` (the prompts of #1328). The spotdown variant
also sees #1330's downbeat-lock hints on the trusted grid; the local tracks have none.
"""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Final

from mowl import track

from classbeat.agent.loop import AgentConfig, run_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.studio_design import StudioContext, StudioResult, studio_stage
from classbeat.agent.studio_mix import Composed, StudioTrack
from classbeat.evals.cases import Case
from classbeat.evals.checks import Check, run_checks
from classbeat.evals.runner import EvalHost
from classbeat.jobs.errors import JobFailure
from classbeat.studio.mix_plan_policy import MIN_PLAYED_SEC, total_sec
from classbeat.studio.mix_repeat_calc import runs

REFERENCE_MARK: Final = "ref-"  # what a catalogue reference looks like in the fakes


def load_studio_cases() -> list[Case]:
    folder = resources.files("classbeat.evals") / "smoke_studio"
    items = sorted((p for p in folder.iterdir() if p.name.endswith(".json")), key=lambda p: p.name)
    return [Case.model_validate_json(p.read_text("utf-8")) for p in items]


def studio_tracks(count: int) -> tuple[StudioTrack, ...]:
    out: list[StudioTrack] = []
    for n in range(count):
        bpm, duration, first = 124.0 + 2 * (n % 4), 240.0 + 15 * (n % 3), 0.3 + 0.05 * n
        phrase = 16 * 240.0 / bpm
        drops = [
            {"t": round(first + phrase * k, 3), "kind": "drop", "song": 0}
            for k in range(1, int(duration / phrase))
        ]
        song = {
            "start": 0, "end": duration, "bpm": bpm, "first_downbeat": first,
            "first_phrase": first, "phrase_bars": 8, "grid_score": 0.9, "beat1_margin": 0.9,
        }  # fmt: skip
        sections = [
            {"start": 0.0, "end": duration / 2, "energy": 0.0},
            {"start": duration / 2, "end": duration, "energy": 1.0},
        ]
        music_map = {"duration": duration, "songs": [song], "events": drops,
                     "sections": sections, "track_version": track.TRACK_VERSION}  # fmt: skip
        out.append(StudioTrack(f"Studio Song {n}", "Invented Band", music_map))
    return tuple(out)


def studio_checks(composed: Composed, count: int, sent: str) -> list[Check]:
    plan = composed.plan
    covered = sorted({t["track"] for t in plan["tracks"]})
    windows = [t["end_sec"] - t["start_sec"] for t in plan["tracks"]]
    played = [sum(windows[a : b + 1]) for a, b in runs([str(t["track"]) for t in plan["tracks"]])]
    total = total_sec(windows, plan["crossfades"])
    detail = f"{len(covered)} of {count} tracks in {len(windows)} pieces"
    return [
        Check("mix_order", covered == list(range(count)), detail),
        Check("mix_windows", min(played) >= MIN_PLAYED_SEC, f"shortest run {min(played):.2f} s"),
        Check(
            "mix_total",
            total == composed.duration_sec == composed.map["duration"],
            f"{total} s from the windows less the crossfades",
        ),
        Check("no_reference", REFERENCE_MARK not in sent, "nothing like a reference was sent"),
    ]


@dataclass(frozen=True)
class StudioCaseResult:
    name: str
    outcome: str
    checks: tuple[Check, ...] = ()
    prompt_version: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome == "submitted" and all(c.ok for c in self.checks)


async def run_studio_case(
    case: Case,
    provider: ModelProvider,
    config: AgentConfig,
    sent: Callable[[], str] | None = None,
    tracks: tuple[StudioTrack, ...] | None = None,
) -> StudioCaseResult:
    """``sent``: a callable giving everything the provider was sent, for the reference check;
    ``tracks``: the approved list's analyses (default ``studio_tracks``; the spotdown variant,
    #1302, gives the same tracks as ``spotdown_map_policy`` maps them)."""
    tracks = tracks if tracks is not None else studio_tracks(case.music.tracks)
    ctx = StudioContext(case.brief, tracks)
    stage = studio_stage(ctx)
    try:
        outcome = await run_stage(EvalHost(), provider, stage, ctx.first_message(), config)
    except JobFailure as failure:
        return StudioCaseResult(case.name, failure.category)
    if not isinstance(outcome.output, StudioResult):
        return StudioCaseResult(case.name, "internal")
    composed = outcome.output.composed
    checks = run_checks(
        case,
        outcome.output.result,
        outcome,
        config.limits,
        composed.duration_sec,
        music_map=composed.map,
    )
    text = sent() if sent is not None else ctx.first_message()
    checks += studio_checks(composed, len(tracks), text)
    return StudioCaseResult(case.name, "submitted", tuple(checks), stage.prompt_version)
