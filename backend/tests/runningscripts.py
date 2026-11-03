"""The recorded fake-provider scripts of the running cases (ADR 0018, issue #465): what a careful
model would do for a running brief (read the music, preview a running draft, submit it), as turns
of tool calls with fixed usage numbers, built like ``smokescripts``.

- ``running_draft`` lays out a running class from the running prompt's guide: a warm-up through
  zones 1 and 2, work blocks in zones 3 to 5 with an easy recovery between them, one easy
  cooldown; ``incline`` puts the work efforts at gradient 1, ``steady`` holds one easy zone;
- ``run_bike_words_30``: the brief asks for cycling things (standing climbs, a cadence, an FTP
  test); the model's first preview carries a cadence and a position and is refused
  ``invalid_input`` by the running schema, the second is a running draft;
- ``run_revise_easier``: the base is a moderate interval run, the revised draft keeps its length
  and cooldown and takes every effort to zone 3 or below;
- the running ``class_patterns`` cases: ``DEVIATES`` gives the brief what it asked for, and
  ``corrected_draft`` is what a model that "corrects" the class back to the pattern submits.

Every number is invented from the running prompt's guide and the running profiles' bounds, never
from a class of the source library.
"""

import copy
from typing import Any

from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.evals.cases import Case


def riv(sec: int, zone: int, gradient: int = 0) -> dict[str, Any]:
    return {"sec": sec, "zone": zone, "gradient": gradient}


def rseg(name: str, kind: str, *intervals: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "kind": kind, "intervals": list(intervals)}


# intensity -> (work zone, top zone or 0, recovery zone)
EFFORT: dict[str, tuple[int, int, int]] = {
    "easy": (3, 0, 2),
    "moderate": (4, 0, 2),
    "hard": (4, 5, 2),
}


def _draft(name: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": f"Invented {name.replace('_', ' ')} run",
        "description": "A running class made for the running cases.",
        "segments": segments,
    }


def _warmup(sec: int) -> dict[str, Any]:
    return rseg("Warm-up", "warmup", riv(sec // 2, 1), riv(sec - sec // 2, 2))


def _cooldown(sec: int) -> dict[str, Any]:
    return rseg("Cooldown", "cooldown", riv(sec, 1))


def running_draft(
    name: str, total: int, *, intensity: str = "moderate", incline: bool = False
) -> dict[str, Any]:
    """A warm-up, blocks of zone 3, a work effort (at gradient 1 with ``incline``), a top effort
    (hard only) and an easy recovery, a tempo for the rest (zone 3, a hard run's in its work
    zone), one cooldown."""
    work, top, easy = EFFORT[intensity]
    warm, cool = (480, 240) if total >= 1800 else (300, 180)
    block = (
        [riv(90, 3), riv(150, work, int(incline)), riv(60, top)]
        if top
        else [
            riv(150, 3),
            riv(120, work, int(incline)),
        ]
    )
    block.append(riv(90, easy))
    size = sum(i["sec"] for i in block)
    count = max(1, min(5, (total - warm - cool - 120) // size))
    segments = [_warmup(warm)]
    segments += [rseg(f"Block {k + 1}", "main", *copy.deepcopy(block)) for k in range(count)]
    tempo = work if top else 3  # a hard run's tempo is a work effort too
    segments.append(rseg("Tempo", "main", riv(total - warm - cool - count * size, tempo)))
    segments.append(_cooldown(cool))
    return _draft(name, segments)


def steady_draft(name: str, total: int) -> dict[str, Any]:
    """One steady easy run in zone 2, with a short build and a cooldown."""
    warm, cool = 300, 240
    return _draft(
        name,
        [
            _warmup(warm),
            rseg("Steady", "main", riv(total - warm - cool, 2)),
            _cooldown(cool),
        ],
    )


def bike_words_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """The first, refused preview of ``run_bike_words_30``: a cadence and a position on a work
    interval, as a model that took the brief's cycling words literally would write."""
    out = copy.deepcopy(draft)
    out["segments"][1]["intervals"][1].update(rpm=[70, 80], standing=True)
    return out


# --- class_patterns (ADR 0017 section 5): the brief wins over the running rules ---------------


def hard_start_draft(case: Case) -> dict[str, Any]:
    """Hard from the first minute: zone 4 at 40 s, inside a 100 s warm-up, then interval blocks."""
    total = case.brief.length_min * 60
    segments = [rseg("Warm-up", "warmup", riv(40, 2), riv(60, 4))]
    block = [riv(180, 4), riv(60, 5), riv(120, 2)]
    segments += [rseg(f"Block {k + 1}", "main", *copy.deepcopy(block)) for k in range(3)]
    segments.append(rseg("Tempo", "main", riv(total - 100 - 3 * 360 - 180, 3)))
    segments.append(_cooldown(180))
    return _draft(case.name, segments)


def capped_draft(case: Case) -> dict[str, Any]:
    """Every effort at zone 3 or below: an aerobic interval run with incline for the work."""
    total = case.brief.length_min * 60
    segments = [_warmup(480)]
    block = [riv(240, 3, 1), riv(120, 2)]
    segments += [rseg(f"Block {k + 1}", "main", *copy.deepcopy(block)) for k in range(5)]
    segments.append(rseg("Tempo", "main", riv(total - 480 - 5 * 360 - 240, 3)))
    segments.append(_cooldown(240))
    return _draft(case.name, segments)


DEVIATES = {
    "cp_run_hard_start_30": hard_start_draft,
    "cp_run_capped_45": capped_draft,
}


def corrected_draft(case: Case) -> dict[str, Any]:
    """The class a model that "corrects" the brief away submits: the profile's ordinary interval
    run (an eight minute warm-up and zone 4 and 5 efforts) at the case's length."""
    return running_draft(case.name, case.brief.length_min * 60, intensity="hard")


# --- the drafts and scripts of each case ------------------------------------------------------

REVISED_NOTE = "Took every effort down to zone 3 or below."


def good_draft(case: Case) -> dict[str, Any]:
    b = case.brief
    total = b.length_min * 60
    if case.name in DEVIATES:
        return DEVIATES[case.name](case)
    if case.revise is not None:
        base = case.revise.base.model_dump(mode="json")
        out = copy.deepcopy(base)
        for seg in out["segments"]:
            for i in seg["intervals"]:
                i["zone"] = min(i["zone"], 3)
        out["name"] = f"Invented {case.name.replace('_', ' ')} run"
        return out
    if case.expect.incline_min_sec is not None:
        return running_draft(case.name, total, intensity=b.intensity, incline=True)
    if case.name.startswith("run_steady"):
        return steady_draft(case.name, total)
    return running_draft(case.name, total, intensity=b.intensity)


def _usage(inp: int, out: int, cached: int = 0) -> dict[str, int]:
    return {"input": inp, "output": out, "cache_read": cached, "cache_write": 0}


def _turn(name: str, input: dict[str, Any], usage: dict[str, int], **checks: Any) -> dict[str, Any]:
    return {"calls": [{"name": name, "input": input, **checks}], "usage": usage}


def script_for(case: Case, draft: dict[str, Any] | None = None) -> FakeScript:
    """The case's recorded script; with ``draft``, the same turns submitting that draft."""
    chosen = good_draft(case) if draft is None else draft
    drafts: dict[str, Any] = {"good": chosen}
    read = _turn("get_music", {}, _usage(4200, 150), expect={"ok": True}, contains=['"tracks"'])
    if case.music.titles or case.music.artists:
        read["calls"][0]["contains"] = ["PWNED", "admin mode"]  # the text arrives as data
    good = {"draft": {"$draft": "good"}}
    preview = _turn(
        "preview_draft",
        good,
        _usage(6300, 2400),
        expect={"ok": True, "tss_approximate": True},
        contains=["quality", '"gradient"'],
    )
    submit_in: dict[str, Any] = dict(good)
    if case.revise is not None:
        submit_in["note"] = REVISED_NOTE
    submit = _turn("submit_draft", submit_in, _usage(8900, 300, 4000), expect={"accepted": True})
    turns = [read, preview, submit]
    if case.name == "run_bike_words_30":
        drafts["bike"] = bike_words_draft(chosen)
        refused = _turn(
            "preview_draft",
            {"draft": {"$draft": "bike"}},
            _usage(6300, 2400),
            expect={"ok": False, "error": "invalid_input"},
        )
        turns = [read, refused, preview, submit]
    return FakeScript.model_validate({"model": "fake-model", "drafts": drafts, "turns": turns})


def provider_for_case(case: Case, draft: dict[str, Any] | None = None) -> FakeProvider:
    return FakeProvider(script_for(case, draft))
