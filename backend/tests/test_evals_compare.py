"""``python -m classbeat.evals compare`` (issue #134): offline, from saved results to ``compare.md``
and a self-contained, escaped ``compare.html``. Results come from real ``run_case`` runs against
``FakeProvider`` scripts, relabelled as other models; hostile files are hand-written."""

import asyncio
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentfake import CONFIG
from classbeat.evals.cases import load_cases
from classbeat.evals.checks import Check
from classbeat.evals.compare import main_compare
from classbeat.evals.runner import CaseResult, results_json, run_case
from smokescripts import provider_for_case

BY_NAME = {c.name: c for c in load_cases()}


async def real(name: str) -> CaseResult:
    case = BY_NAME[name]
    return await run_case(case, provider_for_case(case), CONFIG)


def relabel(r: CaseResult, model: str, effort: str, repeat: int, **changes: Any) -> CaseResult:
    return dataclasses.replace(r, model=model, effort=effort, repeat=repeat, **changes)


def write(path: Path, results: list[CaseResult]) -> Path:
    path.write_text(results_json(results), "utf-8")
    return path


def compare(tmp_path: Path, *files: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main_compare(list(files), tmp_path / "out", out, err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
async def two_models(tmp_path: Path) -> tuple[Path, Path]:
    easy, default = await real("easy_30"), await real("default_30")
    failed_tss = dataclasses.replace(
        easy, checks=[*easy.checks[:-1], Check("tss", False, "TSS 30.0, bounds 40 to 75")]
    )
    assert easy.passed
    assert not failed_tss.passed
    opus = [
        relabel(easy, "claude-opus-5-5", "medium", 0, cost_micros=200_000, wall_sec=20),
        relabel(easy, "claude-opus-5-5", "medium", 1, cost_micros=300_000, wall_sec=30),
        relabel(default, "claude-opus-5-5", "medium", 0, cost_micros=100_000, wall_sec=10),
    ]
    haiku = [
        relabel(easy, "claude-haiku-5-5", "low", 0, cost_micros=10_000, wall_sec=5),
        relabel(failed_tss, "claude-haiku-5-5", "low", 1, cost_micros=30_000, wall_sec=7),
        relabel(default, "claude-haiku-5-5", "low", 0, cost_micros=20_000, wall_sec=6),
    ]
    return write(tmp_path / "opus.json", opus), write(tmp_path / "haiku.json", haiku)


async def test_markdown_has_per_case_rows_and_totals_per_model(
    tmp_path: Path, two_models: tuple[Path, Path]
) -> None:
    code, out, err = compare(tmp_path, *two_models)
    assert (code, err) == (0, "")
    assert "compare.md" in out
    md = (tmp_path / "out" / "compare.md").read_text("utf-8")
    unescaped = md.replace(r"\(", "(").replace(r"\)", ")")  # parentheses are escaped in cells
    rows = {tuple(c.strip() for c in line.split("|")[1:-1]) for line in unescaped.splitlines()}
    opus_easy = next(r for r in rows if r[:2] == ("easy_30", "claude-opus-5-5 (medium)"))
    assert opus_easy[2] == "2/2 (100 %)"
    assert opus_easy[3] == "$0.2500"  # the median of $0.20 and $0.30
    assert opus_easy[5] == "25 s"
    haiku_easy = next(r for r in rows if r[:2] == ("easy_30", "claude-haiku-5-5 (low)"))
    assert haiku_easy[2] == "1/2 (50 %)"
    assert haiku_easy[7] == "tss"
    assert re.search(r"\d+\.\d \(\d+ to \d+\)", haiku_easy[6])  # TSS against its bounds
    # Totals: Opus $0.60 over 3 passed, Haiku $0.06 over 2 passed.
    assert ("claude-opus-5-5 (medium)", "3", "3", "100 %", "$0.6000", "$0.2000") in rows
    assert ("claude-haiku-5-5 (low)", "3", "2", "67 %", "$0.0600", "$0.0300") in rows


async def test_cost_per_passed_case_is_a_dash_when_nothing_passed(tmp_path: Path) -> None:
    easy = await real("easy_30")
    broken = dataclasses.replace(easy, outcome="limit_exceeded", checks=[], timeline=[])
    path = write(tmp_path / "r.json", [relabel(broken, "claude-haiku-5-5", "", 0, cost_micros=5)])
    assert compare(tmp_path, path)[0] == 0
    md = (tmp_path / "out" / "compare.md").read_text("utf-8")
    assert "| claude-haiku-5-5 | 1 | 0 | 0 % | $0.0000 | - |" in md
    assert "limit_exceeded" in md
    html = (tmp_path / "out" / "compare.html").read_text("utf-8")
    assert "no class drawn" in html


async def test_html_draws_each_models_class_on_one_scale(
    tmp_path: Path, two_models: tuple[Path, Path]
) -> None:
    assert compare(tmp_path, *two_models)[0] == 0
    html = (tmp_path / "out" / "compare.html").read_text("utf-8")
    assert html.count("<section>") == 2
    assert "<h3>claude-opus-5-5 (medium)</h3>" in html
    assert "<h3>claude-haiku-5-5 (low)</h3>" in html
    assert html.count("<svg viewBox") == 4  # two cases x two models
    assert 'class="z5"' in html or 'class="z4"' in html
    assert 'class="song"' in html  # a song boundary
    assert "run 1 of 2" in html


async def test_html_is_self_contained_and_escapes_every_string_from_the_file(
    tmp_path: Path,
) -> None:
    easy = await real("easy_30")
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    segs = [dict(s, name=evil) for s in easy.timeline]
    row = relabel(easy, evil, "<b>", 0, timeline=segs)
    row = dataclasses.replace(row, name="x<i>y")
    path = write(tmp_path / "r.json", [row])
    assert compare(tmp_path, path)[0] == 0
    md = (tmp_path / "out" / "compare.md").read_text("utf-8")
    assert "<script" not in md
    assert "&lt;script&gt;" in md
    html = (tmp_path / "out" / "compare.html").read_text("utf-8")
    assert "<script" not in html
    assert "<img" not in html
    assert "<i>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html
    assert "http:" not in html
    assert "https:" not in html  # no other host: not an asset, not a link, not an xmlns
    assert not re.search(r"<[a-z]+[^>]*\b(src|href)=|url\(|@import|<link|<iframe|<form", html)
    assert "default-src 'none'; style-src 'unsafe-inline'" in html


async def test_a_pipe_or_newline_in_a_name_cannot_break_the_markdown_table(
    tmp_path: Path,
) -> None:
    easy = await real("easy_30")
    path = write(tmp_path / "r.json", [relabel(easy, "a|b\nc", "", 0)])
    assert compare(tmp_path, path)[0] == 0
    md = (tmp_path / "out" / "compare.md").read_text("utf-8")
    assert "a\\|b c" in md


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"format": 1}',
        '{"format": 2, "results": []}',
        '{"format": 1, "results": []}',
        '{"format": 1, "results": [{"case": "x"}]}',
        '{"format": 1, "results": [{"case": "x", "model": "m", "passed": true, '
        '"outcome": "PLANTED-SECRET", "cost_micros": -1}]}',
    ],
)
def test_a_bad_file_is_refused_without_echoing_its_content(tmp_path: Path, content: str) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(content, "utf-8")
    code, out, err = compare(tmp_path, bad)
    assert code == 2
    assert out == ""
    assert "PLANTED-SECRET" not in err
    assert not (tmp_path / "out" / "compare.html").exists()


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    code, _, err = compare(tmp_path, tmp_path / "nope.json")
    assert code == 2
    assert "nope.json" in err


async def test_the_command_runs_without_settings_secrets_or_network(
    tmp_path: Path, two_models: tuple[Path, Path]
) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLASSBEAT_")}
    env["CLASSBEAT_SECRETS_DIR"] = str(tmp_path / "no-secrets")
    env["CLASSBEAT_MODEL_PROVIDER"] = "no-such-provider"  # Settings() would refuse it
    out_dir = tmp_path / "cli-out"
    command = [sys.executable, "-m", "classbeat.evals", "compare", *map(str, two_models)]
    result = await asyncio.to_thread(
        subprocess.run,
        [*command, "--out", str(out_dir)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.dumps(sorted(p.name for p in out_dir.iterdir())) == '["compare.html", "compare.md"]'


def raw_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case": "easy_30",
        "model": "m",
        "passed": True,
        "outcome": "submitted",
        "cost_micros": 1,
    }
    return row | changes


def raw_file(path: Path, rows: list[dict[str, Any]], fmt: int = 1) -> Path:
    path.write_text(json.dumps({"format": fmt, "results": rows}), "utf-8")
    return path


def test_deeply_nested_json_is_refused_with_one_line(tmp_path: Path) -> None:
    bad = tmp_path / "deep.json"
    bad.write_text("[" * 100_000, "utf-8")
    code, out, err = compare(tmp_path, bad)
    assert (code, out) == (2, "")
    assert err.count("\n") == 1
    assert "deep.json" in err
    assert "RecursionError" in err


def test_a_file_over_the_size_limit_is_refused(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("classbeat.evals.compare.MAX_FILE_BYTES", 10)
    path = raw_file(tmp_path / "big.json", [raw_row()])
    code, out, err = compare(tmp_path, path)
    assert (code, out) == (2, "")
    assert "big.json" in err
    assert "larger than" in err


def test_an_unsupported_results_format_is_refused(tmp_path: Path) -> None:
    path = raw_file(tmp_path / "v2.json", [raw_row()], fmt=2)
    code, out, err = compare(tmp_path, path)
    assert (code, out) == (2, "")
    assert "results format 2 is not supported" in err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["tss", "wall_sec", "tss_bounds"])
def test_a_non_finite_number_is_refused(tmp_path: Path, field: str, bad: str) -> None:
    value = f"[{bad}, 75]" if field == "tss_bounds" else bad
    row = json.dumps(raw_row()).replace("}", f', "{field}": {value}}}')
    path = tmp_path / "nan.json"
    path.write_text(f'{{"format": 1, "results": [{row}]}}', "utf-8")
    code, out, err = compare(tmp_path, path)
    assert (code, out) == (2, "")
    assert "invalid fields" in err


def test_markdown_link_characters_in_a_file_string_are_escaped(tmp_path: Path) -> None:
    path = raw_file(tmp_path / "r.json", [raw_row(model="[x](http://evil.example)")])
    assert compare(tmp_path, path)[0] == 0
    md = (tmp_path / "out" / "compare.md").read_text("utf-8")
    assert r"\[x\]\(http://evil.example\)" in md
    assert "[x](" not in md


def test_a_backslash_cannot_cancel_the_link_escape(tmp_path: Path) -> None:
    bs = chr(92)
    evil = f"{bs}[x{bs}](http://evil.example)"  # a backslash-prefixed link
    path = raw_file(tmp_path / "r.json", [raw_row(model=evil)])
    assert compare(tmp_path, path)[0] == 0
    md = (tmp_path / "out" / "compare.md").read_text("utf-8")
    # Each backslash is doubled, so the escape after it still applies.
    expected = f"{bs * 3}[x{bs * 3}]{bs}(http://evil.example{bs})"
    assert expected in md


def test_failed_check_names_and_the_outcome_are_escaped_in_the_html(tmp_path: Path) -> None:
    evil = "<b>chk</b>"
    rows = [
        raw_row(passed=False, checks=[{"name": evil, "ok": False}]),
        raw_row(model="n", passed=False, outcome="<i>limit</i>"),
    ]
    path = raw_file(tmp_path / "r.json", rows)
    assert compare(tmp_path, path)[0] == 0
    page = (tmp_path / "out" / "compare.html").read_text("utf-8")
    assert "&lt;b&gt;chk&lt;/b&gt;" in page
    assert "&lt;i&gt;limit&lt;/i&gt;" in page
    assert "<b>" not in page
    assert "<i>" not in page


def test_an_unknown_segment_kind_cannot_choose_a_css_class(tmp_path: Path) -> None:
    segs = [
        {"name": "s", "kind": kind, "intervals": [{"sec": 60, "zone": 3, "track": 0}]}
        for kind in ("warmup", "main", "cooldown", 'x" onload="y', "other")
    ]
    path = raw_file(tmp_path / "r.json", [raw_row(timeline=segs)])
    assert compare(tmp_path, path)[0] == 0
    page = (tmp_path / "out" / "compare.html").read_text("utf-8")
    classes = re.findall(r'class="(k-[^"]*)"', page)
    assert classes == ["k-warmup", "k-main", "k-cooldown", "k-main", "k-main"]
