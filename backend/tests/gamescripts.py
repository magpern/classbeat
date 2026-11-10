"""Recorded drafts for the game cases of the suite (ADR 0029; issue #878): the generic game
requests, the short game fitting must not take under 10 s, and the article's editor operations
(G11) applied to a revise case's base. ``broken_edit`` adds a change to another interval."""

import copy
from typing import Any

from classbeat.evals.cases import Case
from smokedrafts import make_draft

# The type a careful model picks for a request that names none.
GENERIC: dict[str, str] = {
    "games_any_challenge_40": "power_pull",
    "games_any_pacing_35": "overflow",
    "games_any_team_race_30": "racetrack",
}
MARGIN_GAME_SEC = 20  # fits to 17 s on the case's music; a 12 s game fits to under 10 s
MARGIN_LEAD_SEC = 60


def margin_draft(case: Case, game_sec: int = MARGIN_GAME_SEC) -> dict[str, Any]:
    """A class with one short game after a 60 s lead (the music pulls a boundary by seconds)."""
    b = case.brief
    draft = make_draft(
        b.length_min * 60,
        intensity=b.intensity,
        climbs=b.climbs,
        games=("racetrack",),
        name=f"Invented {case.name.replace('_', ' ')} class",
    )
    for seg in draft["segments"]:
        for k, i in enumerate(seg["intervals"]):
            if i.get("game"):
                after = seg["intervals"][k + 1]
                after["sec"] += (
                    i["sec"] - game_sec + seg["intervals"][k - 1]["sec"] - MARGIN_LEAD_SEC
                )
                i["sec"] = game_sec
                seg["intervals"][k - 1]["sec"] = MARGIN_LEAD_SEC
                seg["intervals"][k - 1]["zone"] = 5
    return draft


def _find(draft: dict[str, Any], at: int) -> tuple[dict[str, Any], int]:
    n = 0
    for seg in draft["segments"]:
        if at < n + len(seg["intervals"]):
            return seg, at - n
        n += len(seg["intervals"])
    raise AssertionError(f"no interval {at}")


def edited(case: Case) -> dict[str, Any]:
    """What a careful model hands back for the case's game edit."""
    want = case.expect.revision
    assert case.revise is not None
    assert want is not None
    assert want.at is not None
    out = copy.deepcopy(case.revise.base.model_dump(mode="json", exclude_none=True))
    seg, k = _find(out, want.at)
    ivs, target = seg["intervals"], seg["intervals"][k]
    if want.kind == "game_added":
        ivs[k]["sec"] -= 60  # the new interval takes its minute from the one it precedes
        ivs.insert(
            k,
            {
                "sec": 60,
                "zone": 5,
                "rpm": [85, 95],
                "standing": False,
                "cue": "",
                "game": want.game,
            },
        )
    elif want.kind in ("game_replaced", "game_type"):
        target["game"] = want.game
    elif want.kind == "game_removed":
        target.pop("game")
    elif want.kind == "game_length":
        ivs[k - 1]["sec"] -= (want.sec or 0) - target["sec"]
        target["sec"] = want.sec
    else:
        target["zone"] = want.zone
    return out


def broken_edit(case: Case) -> dict[str, Any]:
    """The edit made, and the first interval's zone changed too (something it was not asked)."""
    out = edited(case)
    first = out["segments"][0]["intervals"][0]
    first["zone"] = first["zone"] + 1
    return out
