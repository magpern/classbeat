"""Drafts for the smoke cases' fake scripts: what a good model would hand to ``submit_draft``.

``make_draft`` assembles a plausible class for a brief from the building blocks of the design
prompt (a warm-up, climbs, sprints, races, an FTP test, a short zone-1 recovery between blocks,
one easy cooldown); the intervals add up to the asked length exactly, because fitting keeps the
total. Cadences are explicit where a block depends on them and ``"beat"`` elsewhere. The cases'
scripts in ``smokescripts`` use these and are checked by ``tests/test_evals_smoke.py``.
"""

from typing import Any

Interval = dict[str, Any]

# Zone of the filler blocks and the number of one-minute zone 5 efforts, per intensity.
PROFILE: dict[str, dict[str, Any]] = {
    "easy": {"hi": 2, "lo": 1, "climb": 3, "burst": 0},
    "moderate": {"hi": 3, "lo": 2, "climb": 4, "burst": 0},
    "hard": {"hi": 4, "lo": 3, "climb": 4, "burst": 3},
}


def iv(sec: int, zone: int, rpm: Any = "beat", standing: bool = False, **extra: Any) -> Interval:
    return {"sec": sec, "zone": zone, "rpm": rpm, "standing": standing, **extra}


def seg(name: str, kind: str, *intervals: Interval) -> dict[str, Any]:
    return {"name": name, "kind": kind, "intervals": list(intervals)}


def _length(segments: list[dict[str, Any]]) -> int:
    return sum(i["sec"] for s in segments for i in s["intervals"])


def make_draft(
    total: int,
    *,
    intensity: str = "moderate",
    climbs: int = 0,
    sprints: int = 0,
    races: int = 0,
    ftp: int | None = None,
    ftp_lead: int = 300,
    games: tuple[str, ...] = (),
    name: str = "Invented smoke class",
    description: str = "A class made for the smoke set.",
) -> dict[str, Any]:
    p = PROFILE[intensity]
    warm = 300 if total >= 1200 else 180
    cool = 270 if total >= 1200 else 150
    segments: list[dict[str, Any]] = [
        seg(
            "Warm-up",
            "warmup",
            iv(warm - warm // 3, 2, [80, 90]),
            iv(warm // 3, 3, [80, 90]),
        )
    ]
    for n in range(climbs):
        segments.append(
            seg(
                f"Climb {n + 1}",
                "main",
                iv(150, p["climb"], [68, 76]),
                iv(30, 5, [65, 75], standing=True),
                iv(90, p["climb"], [68, 76]),
                iv(60, 1, [75, 85]),
            )
        )
    for n in range(sprints):
        segments.append(
            seg(
                f"Sprint {n + 1}",
                "main",
                iv(120, p["hi"], [80, 90]),
                iv(30, 6, [90, 100]),
                iv(60, 1, [75, 85]),
            )
        )
    for n in range(races):
        segments.append(
            seg(
                f"Race {n + 1}",
                "main",
                iv(150, 4, [80, 90]),
                iv(25, 7, [95, 105]),
                iv(60, 1, [80, 90]),
            )
        )
    for n, game in enumerate(games):
        segments.append(
            seg(
                f"Game {n + 1}",
                "main",
                iv(120, 3, [80, 90]),
                iv(60, 5, [85, 95], game=game),
                iv(60, 1, [75, 85]),
            )
        )
    if ftp:
        segments.append(
            seg(
                "FTP test",
                "main",
                iv(ftp_lead, 3, [80, 90]),
                iv(ftp * 60, 5, [80, 90], ftp_test=ftp),
                iv(180, 1, [75, 85]),
            )
        )
    cooldown = seg("Cooldown", "cooldown", iv(cool - 120, 2, [80, 90]), iv(120, 1, [70, 80]))
    # Filler blocks of tempo riding take up what is left; the last one absorbs the remainder.
    left = total - _length([*segments, cooldown])
    bursts = p["burst"] * max(total // 900, 1)
    n = 0
    while left > 0:
        block = min(300, left)
        if left - block < 120:
            block = left
        ivs = [
            iv(block - min(block // 3, 90), p["hi"], [80, 90]),
            iv(min(block // 3, 90), p["lo"], [80, 90]),
        ]
        if n < bursts and block >= 180:
            ivs = [
                iv(block - 120, p["hi"], [80, 90]),
                iv(60, 5, [85, 95]),
                iv(60, p["lo"], [80, 90]),
            ]
        segments.append(seg(f"Tempo {n + 1}", "main", *ivs))
        left -= block
        n += 1
    segments.append(cooldown)
    return {"name": name, "description": description, "segments": segments}
