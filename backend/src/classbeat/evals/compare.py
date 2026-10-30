"""``python -m classbeat.evals compare RESULT.json [RESULT.json ...] --out DIR`` (issue #134).

Offline: it reads the JSON files ``smoke --out`` wrote and writes two files into ``DIR``. It builds
no provider, reads no settings or secrets and opens no connection; the files are treated as
untrusted data (checked field by field, bounded in size), and a refusal names the file and the
error class only, never a value from it.

- ``compare.md``: per case and model (a model at an effort is one series) the pass rate over the
  repeats, the median cost, turns and time, the TSS against its bounds and the failed checks; per
  model the totals and the cost per passed case.
- ``compare.html``: one self-contained page (inline CSS and SVG, no script, no other host: a
  ``Content-Security-Policy`` of ``default-src 'none'`` says so) that draws, per case, the class
  each model designed side by side on one time scale: a bar per interval whose height and colour
  are its zone, a strip of segments, and a tick where the song under the class changes. Every
  string that came from a file (model ids, case names, segment names, check names) goes through
  ``html.escape``; numbers are formatted from parsed ``int`` and ``float`` values.
"""

import html
import json
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final, TextIO

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from classbeat.evals.runner import RESULTS_FORMAT

REFUSED = 2
MAX_FILE_BYTES: Final = 50_000_000
MICROS = Decimal(1_000_000)
ZONES: Final = 7
VIEW_W: Final = 640
VIEW_H: Final = 150
BARS_H: Final = 110

Short = Annotated[str, StringConstraints(max_length=100)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class Interval(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sec: int = Field(ge=0, le=36_000)
    zone: int = Field(ge=0, le=ZONES + 3)
    standing: bool = False
    track: int = Field(ge=-1, le=10_000)


class Segment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, StringConstraints(max_length=80)]
    kind: Annotated[str, StringConstraints(max_length=20)]
    intervals: list[Interval] = Field(max_length=2000)


class CheckRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Short
    ok: bool
    detail: Annotated[str, StringConstraints(max_length=400)] = ""


class Run(BaseModel):
    """One row of ``results_json``."""

    model_config = ConfigDict(extra="ignore")

    case: Annotated[str, StringConstraints(max_length=40)]
    repeat: int = Field(default=0, ge=0, le=1000)
    model: Short
    effort: Annotated[str, StringConstraints(max_length=20)] = ""
    passed: bool
    outcome: Short
    turns: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    corrections: int = Field(default=0, ge=0)
    cost_micros: int = Field(ge=0)
    wall_sec: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    duration_sec: int = Field(default=0, ge=0)
    tss: float = Field(default=0.0, allow_inf_nan=False)
    tss_bounds: tuple[Finite, Finite] | None = None
    changes_on_music: Annotated[str, StringConstraints(max_length=30)] = ""
    checks: list[CheckRow] = Field(default_factory=lambda: list[CheckRow](), max_length=50)
    timeline: list[Segment] = Field(default_factory=lambda: list[Segment](), max_length=200)

    @property
    def series(self) -> str:
        return f"{self.model} ({self.effort})" if self.effort else self.model

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


class ResultsFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: int
    results: list[Run] = Field(max_length=10_000)


class CompareRefused(Exception):
    """A results file cannot be used; the message names the file and the reason class only."""


def load_runs(paths: Sequence[Path]) -> list[Run]:
    runs: list[Run] = []
    for path in paths:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise CompareRefused(f"{path}: larger than {MAX_FILE_BYTES // 1_000_000} MB")
            data = json.loads(path.read_text("utf-8"))
            parsed = ResultsFile.model_validate(data)
        except CompareRefused:
            raise
        except (
            OSError,
            ValueError,
            RecursionError,
        ) as exc:  # JSON, unicode, validation: ValueErrors
            what = "invalid fields" if isinstance(exc, ValidationError) else type(exc).__name__
            raise CompareRefused(f"{path}: not a results file ({what})") from None
        if parsed.format != RESULTS_FORMAT:
            raise CompareRefused(f"{path}: results format {parsed.format} is not supported")
        runs += parsed.results
    if not runs:
        raise CompareRefused("no results in the given files")
    return runs


# --- grouping and statistics ------------------------------------------------------------------


def _usd(micros: float) -> str:
    return f"${Decimal(str(micros)) / MICROS:.4f}"


def _first_seen(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


class Group:
    """The runs of one case by one series."""

    def __init__(self, runs: list[Run]) -> None:
        self.runs = runs
        self.passed = sum(r.passed for r in runs)

    @property
    def median_cost(self) -> float:
        return statistics.median(r.cost_micros for r in self.runs)

    @property
    def median_turns(self) -> float:
        return statistics.median(r.turns for r in self.runs)

    @property
    def median_wall(self) -> float:
        return statistics.median(r.wall_sec for r in self.runs)

    @property
    def tss_text(self) -> str:
        scored = [r for r in self.runs if r.outcome == "submitted"]
        if not scored:
            return "-"
        text = f"{statistics.median(r.tss for r in scored):.1f}"
        bounds = scored[0].tss_bounds
        return f"{text} ({bounds[0]:.0f} to {bounds[1]:.0f})" if bounds else text

    @property
    def failures_text(self) -> str:
        counts = Counter(name for r in self.runs for name in r.failed_checks)
        counts.update(r.outcome for r in self.runs if r.outcome != "submitted")
        return ", ".join(f"{n} x{k}" if k > 1 else n for n, k in sorted(counts.items())) or "-"

    @property
    def pass_text(self) -> str:
        return f"{self.passed}/{len(self.runs)} ({100 * self.passed / len(self.runs):.0f} %)"


class Comparison:
    def __init__(self, runs: list[Run]) -> None:
        self.runs = runs
        self.series = _first_seen([r.series for r in runs])
        self.cases = sorted({r.case for r in runs})
        self.groups: dict[tuple[str, str], Group] = {}
        for case in self.cases:
            for series in self.series:
                here = [r for r in runs if r.case == case and r.series == series]
                if here:
                    self.groups[(case, series)] = Group(here)

    def totals(self, series: str) -> tuple[int, int, int]:
        """Runs, passed runs and total cost in micro-dollars of one series."""
        mine = [r for r in self.runs if r.series == series]
        return len(mine), sum(r.passed for r in mine), sum(r.cost_micros for r in mine)


# --- compare.md -------------------------------------------------------------------------------


def _cell(text: str) -> str:
    """A table cell: a ``|`` or a line break in a file's string cannot break the table, and a
    viewer that renders HTML in Markdown gets it escaped, and ``[]()`` cannot make a link."""
    flat = "".join(" " if c in "\r\n" else c for c in text)
    out = html.escape(flat, quote=False).replace("\\", "\\\\")
    for ch in "|[]()":
        out = out.replace(ch, "\\" + ch)
    return out


def markdown(cmp: Comparison) -> str:
    lines = [
        "# Model comparison of the smoke set",
        "",
        f"{len(cmp.runs)} runs, {len(cmp.cases)} cases, {len(cmp.series)} models "
        "(a model at an effort). Medians are over the repeats of a case.",
        "",
        "## Per case",
        "",
        "| case | model | passed | median cost | median turns | median time | "
        "TSS (bounds) | failures |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in cmp.cases:
        for series in cmp.series:
            g = cmp.groups.get((case, series))
            if g is None:
                continue
            lines.append(
                f"| {_cell(case)} | {_cell(series)} | {g.pass_text} | {_usd(g.median_cost)} | "
                f"{g.median_turns:g} | {g.median_wall:.0f} s | {_cell(g.tss_text)} | "
                f"{_cell(g.failures_text)} |"
            )
    lines += [
        "",
        "## Totals per model",
        "",
        "| model | runs | passed | pass rate | total cost | cost per passed case |",
        "|---|---|---|---|---|---|",
    ]
    for series in cmp.series:
        n, passed, cost = cmp.totals(series)
        per_pass = _usd(cost / passed) if passed else "-"
        lines.append(
            f"| {_cell(series)} | {n} | {passed} | {100 * passed / n:.0f} % | "
            f"{_usd(cost)} | {per_pass} |"
        )
    return "\n".join(lines) + "\n"


# --- compare.html -----------------------------------------------------------------------------


def _e(text: str) -> str:
    return html.escape(text, quote=True)


STYLE: Final = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1d2330; --muted: #5d6677; --line: #cfd5e0; --panel: #f4f6fa;
  --z0: #c9ced8; --z1: #9ec5e8; --z2: #6fb3c9; --z3: #86c27a; --z4: #e3c44f;
  --z5: #e8913b; --z6: #d9543f; --z7: #a3243b; --warm: #dde3ee; --main: #b9c6dd;
  --cool: #cfe3d4; --song: #1d2330;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171f; --fg: #e6e9f0; --muted: #98a1b3; --line: #3a4252; --panel: #1d222d;
    --z0: #4a5160; --z1: #3c6a94; --z2: #3a8399; --z3: #4f9a4a; --z4: #b99a2c;
    --z5: #c4721f; --z6: #c2412d; --z7: #d9566e; --warm: #2c3446; --main: #3b4a68;
    --cool: #2f4a3a; --song: #e6e9f0;
  }
}
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.45 system-ui, sans-serif; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin: 28px 0 8px; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.col { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
.col h3 { font-size: .95rem; margin: 0 0 2px; overflow-wrap: anywhere; }
.meta { color: var(--muted); font-size: .85rem; margin: 0 0 6px; }
svg { width: 100%; height: auto; display: block; }
.segs { margin: 6px 0 0; padding-left: 18px; color: var(--muted); font-size: .8rem; }
.fail { color: var(--z6); font-weight: 600; }
.z0 { fill: var(--z0); } .z1 { fill: var(--z1); } .z2 { fill: var(--z2); }
.z3 { fill: var(--z3); } .z4 { fill: var(--z4); } .z5 { fill: var(--z5); }
.z6 { fill: var(--z6); } .z7 { fill: var(--z7); }
.k-warmup { fill: var(--warm); } .k-main { fill: var(--main); } .k-cooldown { fill: var(--cool); }
.axis { stroke: var(--line); stroke-width: 1; } .song { stroke: var(--song); stroke-width: 1.5; }
.legend span { display: inline-block; margin-right: 10px; font-size: .8rem; }
.legend i { display: inline-block; width: 10px; height: 10px; margin-right: 3px; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; }
th, td { border: 1px solid var(--line); padding: 4px 6px; text-align: left; }
.wrap { overflow-x: auto; }
"""


def _zone_class(zone: int) -> str:
    return f"z{min(max(zone, 0), ZONES)}"


def _kind_class(kind: str) -> str:
    return f"k-{kind}" if kind in ("warmup", "main", "cooldown") else "k-main"


def timeline_svg(run: Run, scale_sec: int) -> str:
    """The class drawn on a time scale of ``scale_sec`` (the longest class of the case)."""
    x_per = VIEW_W / max(scale_sec, 1)
    parts = [
        f'<svg viewBox="0 0 {VIEW_W} {VIEW_H}" role="img" '
        f'aria-label="zone over time, {run.duration_sec // 60} minutes">',
        f'<line class="axis" x1="0" y1="{BARS_H + 14}" x2="{VIEW_W}" y2="{BARS_H + 14}"/>',
    ]
    t = 0
    last_track: int | None = None
    song = 0
    for seg in run.timeline:
        seg_start = t
        for i in seg.intervals:
            x, w = t * x_per, max(i.sec * x_per, 0.5)
            if last_track is not None and i.track != last_track:
                song += 1
                parts.append(
                    f'<line class="song" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{BARS_H + 14}">'
                    f"<title>song {song + 1} from {t // 60}:{t % 60:02d}</title></line>"
                )
            last_track = i.track
            h = BARS_H * min(i.zone, ZONES) / ZONES
            parts.append(
                f'<rect class="{_zone_class(i.zone)}" x="{x:.1f}" y="{BARS_H - h:.1f}" '
                f'width="{w:.1f}" height="{h:.1f}"><title>zone {i.zone}, {i.sec} s'
                f"{', standing' if i.standing else ''}</title></rect>"
            )
            t += i.sec
        parts.append(
            f'<rect class="{_kind_class(seg.kind)}" x="{seg_start * x_per:.1f}" '
            f'y="{BARS_H + 2}" width="{max((t - seg_start) * x_per - 1, 0.5):.1f}" height="10">'
            f"<title>{_e(seg.name)} ({_e(seg.kind)}), {t - seg_start} s</title></rect>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _column(run: Run, nth: int, of: int, scale_sec: int) -> str:
    verdict = "pass" if run.passed else '<span class="fail">FAIL</span>'
    failed = ", ".join(_e(n) for n in run.failed_checks) or (
        "" if run.outcome == "submitted" else _e(run.outcome)
    )
    names = "".join(
        f"<li>{_e(s.name)} ({_e(s.kind)}, {sum(i.sec for i in s.intervals)} s)</li>"
        for s in run.timeline
    )
    body = timeline_svg(run, scale_sec) if run.timeline else "<p class='meta'>no class drawn</p>"
    return (
        f'<div class="col"><h3>{_e(run.series)}</h3>'
        f'<p class="meta">run {nth} of {of}: {verdict}, {_usd(run.cost_micros)}, '
        f"{run.turns} turns, {run.wall_sec:.0f} s, TSS {run.tss:.1f}"
        f"{' - ' + failed if failed else ''}</p>{body}"
        f'<ol class="segs">{names}</ol></div>'
    )


def html_page(cmp: Comparison) -> str:
    legend = "".join(
        f'<span><svg width="10" height="10" viewBox="0 0 10 10"><rect class="z{z}" width="10" '
        f'height="10"/></svg> zone {z}</span>'
        for z in range(1, ZONES + 1)
    )
    sections: list[str] = []
    for case in cmp.cases:
        shown: list[tuple[Run, int, int]] = []
        for series in cmp.series:
            g = cmp.groups.get((case, series))
            if g is None:
                continue
            # The first run that drew a class; else the first run, which says why it did not.
            run = next((r for r in g.runs if r.timeline), g.runs[0])
            shown.append((run, g.runs.index(run) + 1, len(g.runs)))
        scale = max((sum(i.sec for s in r.timeline for i in s.intervals) for r, _, _ in shown))
        cols = "".join(_column(r, n, of, scale) for r, n, of in shown)
        sections.append(f'<section><h2>{_e(case)}</h2><div class="cols">{cols}</div></section>')
    rows = "".join(
        f"<tr><td>{_e(s)}</td><td>{n}</td><td>{p}</td><td>{_usd(c)}</td>"
        f"<td>{_usd(c / p) if p else '-'}</td></tr>"
        for s in cmp.series
        for n, p, c in [cmp.totals(s)]
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Model comparison</title>"
        f"<style>{STYLE}</style></head><body><h1>Model comparison of the smoke set</h1>"
        f"<p class='meta'>{len(cmp.runs)} runs. One class per case and model (the first run that "
        "drew one) on the case's time scale: bar height and colour are the zone, the strip below "
        "is the segments, a dark line is where the song changes.</p>"
        f"<p class='legend'>{legend}</p>"
        "<div class='wrap'><table><tr><th>model</th><th>runs</th><th>passed</th>"
        f"<th>total cost</th><th>cost per passed case</th></tr>{rows}</table></div>"
        f"{''.join(sections)}</body></html>\n"
    )


def main_compare(
    paths: Sequence[Path], out_dir: Path, out: TextIO = sys.stdout, err: TextIO = sys.stderr
) -> int:
    try:
        cmp = Comparison(load_runs(paths))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "compare.md").write_text(markdown(cmp), encoding="utf-8")
        (out_dir / "compare.html").write_text(html_page(cmp), encoding="utf-8")
    except CompareRefused as refused:
        print(str(refused), file=err)
        return REFUSED
    except OSError as exc:
        print(f"cannot write {out_dir}: {type(exc).__name__}", file=err)
        return REFUSED
    print(f"wrote {out_dir / 'compare.md'} and {out_dir / 'compare.html'}", file=out)
    return 0
