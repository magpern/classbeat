"""Song-choice quality checks (ADR 0012 section 4.1; issue #208), deterministic and on data the
backend computed: the pool the model submitted, the list it chose and MOWL's analysis of the pool.
Nothing here reads model text.

The hard checks, each one ``(name, ok, detail)``; a case passes when all of them do:

- ``pool_size``: the pool has ``n`` to ``4n`` candidates (``PickPlan.pool_range``);
- ``pool_roles``: every role the brief needs is covered by the candidates the plan asks for
  (``PickPlan.required_roles``: two per slot), and two candidates can open the class;
- ``pool_unique``: no (title, artist) twice in the pool;
- ``pool_years`` (only for a case that names an era): every candidate, and every ``year:`` filter
  of a search, lies in the era;
- ``bpm_band``: each new song's BPM lies in the band of its role (the manifest's ``bpm_bands``: a
  low and an optional high, optionally also at double that, and a number of drops a song needs: a
  song with no drop is not a sprint). A kept song of a replace case is the instructor's: not judged;
- ``energy``: the peak energy of the main part (every role except warmup, recovery and cooldown)
  is above the opening song's and above the closing song's. A song's energy is its BPM plus
  ``DROP_ENERGY`` for each drop MOWL found, the data the model saw. A class with no main part
  passes.

The total within +-10 %, the order (open on a warmup or build, end on a cooldown), no track twice
and a replace keeping its kept songs are checked beside these in ``pick.pick_checks``.

The **scores** (numbers in [0, 1] or a share, written to the results so ``compare`` can rank
models): ``bpm_in_band`` (the share of judged songs inside their band, 1 when none is judged) and
``length_error`` (the total's distance from the asked length, as a share of it).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.choose_songs import ChosenList
from classbeat.agent.songs import PickPlan, key_of, total_sec

DROP_ENERGY: Final = 10.0
MAIN_EXCLUDED: Final = ("warmup", "recovery", "cooldown")
MIN_OPENERS: Final = 2
YEAR_FILTER: Final = re.compile(r"\byear:(\d{4})(?:-(\d{4}))?\b")
SCORE_DIGITS: Final = 4


class Band(BaseModel):
    """The BPM band of one role: ``low`` to ``high`` (no upper limit when ``high`` is absent),
    also at twice those (a climb played in half time), for songs with at least ``min_drops``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    low: int = Field(ge=1, le=300)
    high: int | None = Field(default=None, ge=1, le=300)
    doubles: bool = False
    min_drops: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def _ordered(self) -> "Band":
        if self.high is not None and self.high < self.low:
            raise ValueError("a band's high is not below its low")
        return self

    def holds(self, bpm: float, drops: int) -> bool:
        if drops < self.min_drops:
            return False
        scales = (1, 2) if self.doubles else (1,)
        return any(
            s * self.low <= bpm and (self.high is None or bpm <= s * self.high) for s in scales
        )


@dataclass(frozen=True)
class PoolRow:
    """A pool candidate as the backend knows it (never shown to the model as is)."""

    role: str
    title: str
    artist: str
    year: int


@dataclass(frozen=True)
class SongFacts:
    """What the checks look at besides the plan and the list: the pool, the searches' text and,
    per Spotify id, the drops MOWL found."""

    pool: Sequence[PoolRow]
    queries: Sequence[str]
    drops: Mapping[str, int]
    years: tuple[int, int] | None = None


Row = tuple[str, bool, str]


def _in_years(facts: SongFacts) -> Row | None:
    if facts.years is None:
        return None
    low, high = facts.years
    outside = sum(not low <= r.year <= high for r in facts.pool)
    for q in facts.queries:
        for m in YEAR_FILTER.finditer(q):
            a = int(m[1])
            b = int(m[2] or m[1])
            outside += not (low <= a <= b <= high)
    return "pool_years", outside == 0, f"{outside} outside {low} to {high}"


def energy_of(bpm: float, drops: int) -> float:
    return bpm + DROP_ENERGY * drops


def _energy_row(chosen: ChosenList, facts: SongFacts) -> Row:
    songs = chosen.songs
    energies = [energy_of(s.bpm, facts.drops.get(s.spotify_id, 0)) for s in songs]
    main = [e for s, e in zip(songs, energies, strict=True) if s.role not in MAIN_EXCLUDED]
    if not main:
        return "energy", True, "no main part"
    peak = max(main)
    ok = peak > energies[0] and peak > energies[-1]
    return (
        "energy",
        ok,
        f"peak {round(peak)}, opening {round(energies[0])}, closing {round(energies[-1])}",
    )


def band_judgement(chosen: ChosenList, facts: SongFacts, bands: Mapping[str, Band]) -> list[bool]:
    """Per judged song (new, with a band for its role): inside the band or not."""
    return [
        bands[s.role].holds(s.bpm, facts.drops.get(s.spotify_id, 0))
        for s in chosen.songs
        if s.new and s.role in bands
    ]


def song_checks(
    plan: PickPlan, chosen: ChosenList, facts: SongFacts, bands: Mapping[str, Band]
) -> list[Row]:
    """The hard checks above, in a fixed order, for a list the stages accepted."""
    rows: list[Row] = []
    low, high = plan.pool_range
    rows.append(
        ("pool_size", low <= len(facts.pool) <= high, f"{len(facts.pool)} in {low} to {high}")
    )
    by_role: dict[str, int] = {}
    for r in facts.pool:
        by_role[r.role] = by_role.get(r.role, 0) + 1
    short = {k: v for k, v in plan.required_roles().items() if by_role.get(k, 0) < v}
    openers = by_role.get("warmup", 0) + by_role.get("build", 0)
    if not plan.replace and openers < MIN_OPENERS:
        short["opener"] = MIN_OPENERS
    rows.append(("pool_roles", not short, f"short of {sorted(short)}" if short else "covered"))
    keys = [key_of(r.title, r.artist) for r in facts.pool]
    rows.append(("pool_unique", len(set(keys)) == len(keys), f"{len(keys) - len(set(keys))} twice"))
    years = _in_years(facts)
    if years:
        rows.append(years)
    judged = band_judgement(chosen, facts, bands)
    rows.append(("bpm_band", all(judged), f"{sum(judged)} of {len(judged)} in band"))
    rows.append(_energy_row(chosen, facts))
    return rows


def song_scores(
    plan: PickPlan, chosen: ChosenList, facts: SongFacts, bands: Mapping[str, Band]
) -> dict[str, float]:
    judged = band_judgement(chosen, facts, bands)
    total = total_sec([s.length_sec for s in chosen.songs])
    return {
        "bpm_in_band": round(sum(judged) / len(judged), SCORE_DIGITS) if judged else 1.0,
        "length_error": round(abs(total - plan.target_sec) / plan.target_sec, SCORE_DIGITS),
    }
