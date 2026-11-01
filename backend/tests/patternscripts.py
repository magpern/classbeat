"""The scripted drafts of the ``class_patterns`` cases (ADR 0017 section 5, issue #399): what a
careful model would hand to ``submit_draft`` for each brief, invented from the bounds of the
shipped profiles (never from a class of the source library) with the plain ``smokedrafts`` blocks.

- ``follows`` cases: ``FOLLOWS`` holds the shape of each: a warm-up, cycles of blocks in zones 3,
  4, 5 and 1, a cooldown. The numbers sit inside the case's profile (``test_evals_class_patterns``
  checks that no rule hits).
- ``deviates`` cases: ``DEVIATES`` gives the brief what it asked for.
- ``corrected``: the draft a model that "corrects" the class away from the brief submits.
"""

from typing import Any

from classbeat.evals.cases import Case
from smokedrafts import iv, seg

# case -> (warm-up s, cooldown s, lead-in zone 4 s, cycles, (zone 3, 4, 5, 1 seconds in all))
FOLLOWS: dict[str, tuple[int, int, int, int, tuple[int, int, int, int]]] = {
    "cp_plain_30": (480, 150, 0, 3, (690, 480, 0, 0)),
    "cp_plain_45": (480, 150, 0, 6, (1170, 900, 0, 0)),
    "cp_plain_60": (600, 240, 0, 6, (1380, 1380, 0, 0)),
    "cp_interval_30": (480, 150, 0, 4, (570, 420, 120, 60)),
    "cp_interval_45": (480, 150, 0, 4, (690, 1020, 180, 180)),
    "cp_interval_60": (480, 240, 120, 3, (1380, 840, 360, 180)),
}


def _warmup(sec: int) -> dict[str, Any]:
    return seg("Warm-up", "warmup", iv(sec // 2, 2, [80, 90]), iv(sec - sec // 2, 3, [80, 90]))


def _cooldown(sec: int) -> dict[str, Any]:
    first = sec * 2 // 3
    return seg("Cooldown", "cooldown", iv(first, 2, [80, 90]), iv(sec - first, 1, [70, 80]))


def _draft(case: Case, segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": f"Invented {case.name.replace('_', ' ')} class",
        "description": "A class made for the class-pattern cases.",
        "segments": segments,
    }


def follows_draft(case: Case, like: str | None = None) -> dict[str, Any]:
    """The draft of a ``follows`` case; ``like``: that of another case, at this case's length."""
    warm, cool, lead, n, (z3, z4, z5, z1) = FOLLOWS[like or case.name]
    total = case.brief.length_min * 60
    cycle = [(z3 // n, 3), (z4 // n, 4), (z5 // n, 5), (z1 // n, 1)]
    cycle = [b for b in cycle if b[0] > 0]
    cycles = [list(cycle) for _ in range(n)]
    left = total - warm - cool - lead - sum(s for s, _ in cycle) * n
    cycles[0][0] = (cycles[0][0][0] + left, cycles[0][0][1])  # the remainder rides in zone 3
    segments = [_warmup(warm)]
    if lead:
        segments.append(seg("Lead-in", "main", iv(lead, 4, [80, 90])))
    for k, blocks in enumerate(cycles):
        segments.append(seg(f"Block {k + 1}", "main", *(iv(s, z, [80, 90]) for s, z in blocks)))
    segments.append(_cooldown(cool))
    return _draft(case, segments)


def _cycles(spec: list[tuple[int, int]], times: int) -> list[dict[str, Any]]:
    return [
        seg(f"Block {k + 1}", "main", *(iv(s, z, [80, 90]) for s, z in spec)) for k in range(times)
    ]


def hard_start_draft(case: Case) -> dict[str, Any]:
    """Hard from the first minute: a zone 4 interval 30 s in, inside a two minute warm-up."""
    segments = [seg("Warm-up", "warmup", iv(30, 3, [80, 90]), iv(90, 4, [85, 95]))]
    segments += _cycles([(180, 4), (60, 5), (60, 3)], 4)
    segments.append(seg("Push", "main", iv(150, 4, [85, 95]), iv(30, 5, [90, 100])))
    segments.append(seg("Steady", "main", iv(case.brief.length_min * 60 - 1650, 3, [80, 90])))
    segments.append(_cooldown(150))
    return _draft(case, segments)


def short_warmup_draft(case: Case) -> dict[str, Any]:
    """A warm-up of two minutes, then the class the pattern shows."""
    total = case.brief.length_min * 60
    segments = [seg("Warm-up", "warmup", iv(60, 2, [80, 90]), iv(60, 3, [80, 90]))]
    segments += _cycles([(240, 3), (180, 4), (60, 5), (60, 1)], 4)
    used = 120 + 4 * 540
    segments.append(seg("Tempo", "main", iv(total - used - 150 - 240, 3, [80, 90])))
    segments.append(seg("Push", "main", iv(240, 4, [85, 95])))
    segments.append(_cooldown(150))
    return _draft(case, segments)


def ten_sprints_draft(case: Case) -> dict[str, Any]:
    """Ten sprints: a build, a 20 s burst in zone 6, a short spin, ten times."""
    total = case.brief.length_min * 60
    segments = [_warmup(420)]
    for k in range(10):
        segments.append(
            seg(
                f"Sprint {k + 1}",
                "main",
                iv(60, 4, [85, 95]),
                iv(20, 6, [90, 100]),
                iv(40, 1, [75, 85]),
            )
        )
    segments.append(_cooldown(total - 420 - 1200))
    return _draft(case, segments)


def easy_spin_draft(case: Case) -> dict[str, Any]:
    """An easy spin: zones 1 to 3 only."""
    total = case.brief.length_min * 60
    segments = [_warmup(300)]
    segments += _cycles([(300, 2), (120, 3), (60, 1)], 4)
    used = 300 + 4 * 480 + 150
    segments.append(seg("Steady", "main", iv(total - used, 2, [80, 90])))
    segments.append(_cooldown(150))
    return _draft(case, segments)


DEVIATES = {
    "cp_hard_start_30": hard_start_draft,
    "cp_short_warmup_45": short_warmup_draft,
    "cp_ten_sprints_30": ten_sprints_draft,
    "cp_easy_spin_45": easy_spin_draft,
}


# What a model that "corrects" the class away from the brief submits: the plain pattern at the
# case's own length.
CORRECTED_TO = {
    "cp_hard_start_30": "cp_plain_30",
    "cp_short_warmup_45": "cp_plain_45",
    "cp_ten_sprints_30": "cp_plain_30",
    "cp_easy_spin_45": "cp_plain_45",
}


def corrected_draft(case: Case) -> dict[str, Any]:
    return follows_draft(case, CORRECTED_TO[case.name])


def pattern_draft(case: Case) -> dict[str, Any]:
    if case.name in FOLLOWS:
        return follows_draft(case)
    return DEVIATES[case.name](case)
