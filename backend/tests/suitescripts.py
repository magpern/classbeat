"""The recorded fake-provider script of each design-group suite case (ADR 0012 section 4,
issue #206): what a careful model would do (read the music, preview, submit), as turns of tool
calls with fixed usage numbers, built like ``smokescripts`` so the drafts follow the case.

- ``standard`` and ``games_ftp``: ``smokedrafts.make_draft`` for the brief (and the case's games);
  an FTP test gets the first lead in front of it that both fits the music and leaves the case's
  warm-up (``fitting_lead``).
- ``music_sync``: ``synced_draft`` lays the class out on the music map itself: every zone change
  on a phrase boundary or a music event, every sprint and race on a drop.
- ``revisions``: ``REVISED`` holds what a careful model hands back for each request.

``broken_*`` build the drafts a careless model would submit, for the failing scripts: each breaks
one music-sync or revision rule.
"""

import copy
import itertools
from typing import Any

from mowl import music

from classbeat.agent import DesignContext
from classbeat.agent.design import check
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.evals.cases import Case
from classbeat.evals.music import build_music
from smokedrafts import iv, make_draft, seg

ZONES: dict[str, tuple[int, int, int]] = {  # intensity -> (hi, lo, a zone 5 effort's seconds)
    "easy": (2, 1, 0),
    "moderate": (3, 2, 30),
    "hard": (4, 3, 90),
}


def _usage(inp: int, out: int, cached: int = 0) -> dict[str, int]:
    return {"input": inp, "output": out, "cache_read": cached, "cache_write": 0}


def _turn(name: str, input: dict[str, Any], usage: dict[str, int], **checks: Any) -> dict[str, Any]:
    return {"calls": [{"name": name, "input": input, **checks}], "usage": usage}


def music_map(case: Case) -> dict[str, Any]:
    return DesignContext.build(case.brief, build_music(case.music)).map


def fits(case: Case, draft: dict[str, Any]) -> bool:
    try:
        check(WorkoutDraft.model_validate(draft), music_map(case))
    except ToolError:
        return False
    return True


def fitting_lead(case: Case, **draft: Any) -> int:
    """The first lead before the FTP test, from the case's warm-up on, that fits the music."""
    least = (case.expect.ftp_warmup_min_sec or 300) - 240  # the warm-up is 300 s, less margin
    for lead in range(max(least + 60, 300), 700, 10):
        if fits(case, make_draft(**draft, ftp_lead=lead)):
            return lead
    raise AssertionError(f"{case.name}: no FTP lead fits the music")


def design_draft(case: Case) -> dict[str, Any]:
    b = case.brief
    if case.expect.music_sync is not None:
        return synced_draft(case)
    draft: dict[str, Any] = dict(
        total=b.length_min * 60,
        intensity=b.intensity,
        climbs=b.climbs,
        sprints=b.sprints,
        races=b.races,
        ftp=b.ftp_test,
        games=tuple(case.expect.games),
        name=f"Invented {case.name.replace('_', ' ')} class",
    )
    if b.ftp_test:
        return make_draft(**draft, ftp_lead=fitting_lead(case, **draft))
    return make_draft(**draft)


# --- music sync -------------------------------------------------------------------------------


class Grid:
    """The boundaries of a music map within a class: phrase starts and events, whole seconds."""

    def __init__(self, m: dict[str, Any], total: int) -> None:
        times = [*music.phrases(m), *(float(e["t"]) for e in m["events"])]
        self.bounds = sorted({round(t) for t in times if 0 < t < total})
        self.drops = sorted({round(float(e["t"])) for e in m["events"] if e["kind"] == "drop"})
        self.events = sorted(float(e["t"]) for e in m["events"])

    def near(self, t: float, lo: int, hi: int) -> int:
        """The boundary in (lo, hi) nearest to t."""
        inside = [b for b in self.bounds if lo < b < hi]
        if not inside:
            raise AssertionError(f"no boundary between {lo} and {hi}")
        return min(inside, key=lambda b: abs(b - t))


def _fill(grid: Grid, start: int, end: int, hi: int, lo: int, effort: int) -> list[Any]:
    """Tempo riding from start to end (both boundaries): hi, a zone 5 effort, lo, repeated;
    the last interval is hi, the build-up to what comes at ``end``."""
    out: list[tuple[int, int, list[int]]] = []  # (end, zone, rpm)
    t = start
    while end - t > 360:
        a = grid.near(t + 150, t + 60, end - 120)
        out.append((a, hi, [80, 90]))
        if effort:
            b = grid.near(a + effort, a + effort // 2, a + effort + 16)
            out.append((b, 5, [85, 95]))
            a = b
        c = grid.near(a + 75, a + 30, end - 60)
        out.append((c, lo, [80, 90]))
        t = c
    out.append((end, hi, [80, 90]))
    ivs, t = [], start
    for stop, zone, rpm in out:
        ivs.append(iv(stop - t, zone, rpm))
        t = stop
    return ivs


def synced_draft(case: Case) -> dict[str, Any]:
    b = case.brief
    total = b.length_min * 60
    grid = Grid(music_map(case), total)
    hi, lo, effort = ZONES[b.intensity]
    warm_a = grid.near(200, 100, 260)
    warm_b = grid.near(300, warm_a + 60, 400)
    cool = grid.near(total - 270, total - 330, total - 200)
    cool_b = grid.near(total - 120, cool + 60, total - 60)
    segments = [seg("Warm-up", "warmup", iv(warm_a, 2, [80, 90]), iv(warm_b - warm_a, 3, [80, 90]))]
    bursts = ["sprint"] * b.sprints + ["race"] * b.races
    usable = [d for d in grid.drops if warm_b + 200 < d < cool - 200]
    picks = [
        usable[round(k * (len(usable) - 1) / max(len(bursts) - 1, 1))] for k in range(len(bursts))
    ]
    if len(set(picks)) != len(bursts):
        raise AssertionError(f"{case.name}: too few drops for the sprints")
    t = warm_b
    for n, (kind, drop) in enumerate(zip(bursts, picks, strict=True)):
        build = _fill(grid, t, drop, hi, lo, effort)
        burst_end = grid.near(drop + 30 if kind == "sprint" else drop + 25, drop + 15, drop + 36)
        rec_end = grid.near(burst_end + 75, burst_end + 30, burst_end + 116)
        zone = 6 if kind == "sprint" else 7
        segments.append(
            seg(
                f"{kind.title()} {n + 1}",
                "main",
                *build,
                iv(burst_end - drop, zone, [90, 100]),
                iv(rec_end - burst_end, 1, [80, 90]),
            )
        )
        t = rec_end
    segments.append(seg("Tempo", "main", *_fill(grid, t, cool, hi, lo, effort)))
    segments.append(
        seg("Cooldown", "cooldown", iv(cool_b - cool, 2, [80, 90]), iv(total - cool_b, 1, [70, 80]))
    )
    return {
        "name": f"Invented {case.name.replace('_', ' ')} class",
        "description": "A class laid out on the music.",
        "segments": segments,
    }


def broken_sprint(draft: dict[str, Any], shift: int = 8) -> dict[str, Any]:
    """The first sprint starts ``shift`` seconds after its drop (the build-up runs on)."""
    out = copy.deepcopy(draft)
    for segment in out["segments"]:
        ivs = segment["intervals"]
        for k, i in enumerate(ivs):
            if i["zone"] >= 6 and k:
                ivs[k - 1]["sec"] += shift
                i["sec"] -= shift
                return out
    raise AssertionError("no sprint to move")


def broken_phrase(case: Case, draft: dict[str, Any], shift: int = 5) -> dict[str, Any]:
    """The first zone change with no music event within 12 s moves ``shift`` seconds off its
    phrase (fitting would put it back on an event close by)."""
    grid = Grid(music_map(case), case.brief.length_min * 60)
    out = copy.deepcopy(draft)
    ivs = [i for s in out["segments"] for i in s["intervals"]]
    t = 0
    for before, i in itertools.pairwise(ivs):
        t += before["sec"]
        clear = all(abs(t - e) > 12 for e in grid.events)
        if i["zone"] != before["zone"] and clear and i["sec"] > shift + 10:
            before["sec"] += shift
            i["sec"] -= shift
            return out
    raise AssertionError("no zone change away from the music events")


# --- revisions --------------------------------------------------------------------------------

# What a careful model hands back for each revision case (make_draft's arguments).
REVISED: dict[str, dict[str, Any]] = {
    "rev_more_sprints_30": dict(total=1800, intensity="moderate", climbs=1, sprints=3),
    "rev_more_sprints_60": dict(total=3600, intensity="hard", climbs=1, sprints=3, races=1),
    "rev_easier_40": dict(total=2400, intensity="moderate", climbs=1, sprints=2),
    "rev_easier_50_to_easy": dict(total=3000, intensity="easy", climbs=1),
    "rev_shorter_45_to_30": dict(total=1800, intensity="moderate", climbs=1, sprints=1),
    "rev_shorter_75_to_60": dict(total=3600, intensity="hard", climbs=1, sprints=2),
    "rev_climb_to_end_45": dict(total=2700, intensity="moderate", climbs=1, sprints=2),
    "rev_climb_to_start_40": dict(total=2400, intensity="moderate", climbs=1, sprints=2),
}
NOTES: dict[str, str] = {
    "rev_more_sprints_30": "Added two sprints.",
    "rev_more_sprints_60": "Added a third sprint.",
    "rev_easier_40": "Brought the class down to moderate effort.",
    "rev_easier_50_to_easy": "Made it an easy ride.",
    "rev_shorter_45_to_30": "Cut the class to 30 minutes.",
    "rev_shorter_75_to_60": "Cut the class to an hour.",
    "rev_climb_to_end_45": "Moved the climb to the end.",
    "rev_climb_to_start_40": "Moved the climb to the start.",
}


def move_climb(draft: dict[str, Any], to: str) -> dict[str, Any]:
    """The draft with its climb segment moved to the first or last main place."""
    out = copy.deepcopy(draft)
    segs = out["segments"]
    climb = next(s for s in segs if s["name"].startswith("Climb"))
    segs.remove(climb)
    segs.insert(1 if to == "start" else len(segs) - 1, climb)
    return out


def revised_draft(case: Case) -> dict[str, Any]:
    draft = make_draft(**REVISED[case.name], name=f"Invented {case.name.replace('_', ' ')}")
    want = case.expect.revision
    if want is not None and want.kind == "climb_moved" and want.climb_at == "end":
        return move_climb(draft, "end")
    return draft


# --- scripts ----------------------------------------------------------------------------------


def design_script(case: Case, draft: dict[str, Any]) -> FakeScript:
    read = _turn("get_music", {}, _usage(4200, 150), expect={"ok": True}, contains=['"tracks"'])
    good = {"draft": {"$draft": "good"}}
    preview = _turn(
        "preview_draft", good, _usage(6300, 2400), expect={"ok": True}, contains=["quality"]
    )
    submit = _turn("submit_draft", good, _usage(8900, 300, 4000), expect={"accepted": True})
    return FakeScript.model_validate(
        {"model": "fake-model", "drafts": {"good": draft}, "turns": [read, preview, submit]}
    )


def revise_script(case: Case, draft: dict[str, Any]) -> FakeScript:
    read = _turn("get_music", {}, _usage(5200, 150), expect={"ok": True}, contains=['"tracks"'])
    both = {"draft": {"$draft": "revised"}}
    preview = _turn(
        "preview_draft", both, _usage(7400, 2400), expect={"ok": True}, contains=["quality"]
    )
    submit = _turn(
        "submit_draft",
        {**both, "note": NOTES[case.name]},
        _usage(10000, 300, 5000),
        expect={"accepted": True},
    )
    return FakeScript.model_validate(
        {"model": "fake-model", "drafts": {"revised": draft}, "turns": [read, preview, submit]}
    )


def good_draft(case: Case) -> dict[str, Any]:
    return revised_draft(case) if case.revise is not None else design_draft(case)


def script_for(case: Case, draft: dict[str, Any] | None = None) -> FakeScript:
    """The case's recorded script; with ``draft``, the same turns submitting that draft."""
    chosen = good_draft(case) if draft is None else draft
    if case.revise is not None:
        return revise_script(case, chosen)
    return design_script(case, chosen)


def provider_for_case(case: Case, draft: dict[str, Any] | None = None) -> FakeProvider:
    return FakeProvider(script_for(case, draft))
