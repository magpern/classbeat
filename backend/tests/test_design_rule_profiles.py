"""Rule profiles (ADR 0017 P1): schema, loader, digests, no free text and ``select_profile``."""

import ast
import json
import re
import shutil
import types
from pathlib import Path
from typing import Any

import pytest

from classbeat.design.rules import RATIONALES, ProfileError, load_registry, select_profile
from classbeat.design.rules import enums as E
from classbeat.design.rules import profile as P

PROFILES = P.PROFILES_DIR
RULES_PKG = Path(P.__file__).resolve().parent


def _profile_dict(**over: Any) -> dict[str, Any]:
    d = json.loads((PROFILES / "cycling" / "interval_45.json").read_text())
    d.update(over)
    return d


def _parse(d: dict[str, Any]) -> P.Profile:
    return P.parse_profile(json.dumps(d).encode())


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "profiles"
    shutil.copytree(PROFILES, dst)
    return dst


CYCLING = [f"cycling.{t}.{n}" for t in ("interval", "plain") for n in (30, 45, 60)]
PROVISIONAL = [f"cycling.{t}.55" for t in ("interval", "plain")]
RUNNING = ["running.interval.30", "running.interval.45"]


def test_shipped_profiles_load() -> None:
    reg = load_registry()
    assert sorted(reg.profiles) == sorted(CYCLING + PROVISIONAL + RUNNING)
    assert reg.rules_version == 1
    for p in reg.profiles.values():
        assert p.n >= 1
        assert p.source == E.SOURCE
        assert p.snapshot in ("2026-11-01", "2026-11-03", "2026-11-05")
        assert 0 < len(p.rules) <= P.MAX_RULES
    for pid in CYCLING:
        p = reg.profiles[pid]
        assert p.evidence == "strong"
        assert p.n >= E.MIN_N_PERCENTILE
    # plain classes stay in zones 1 to 4; interval classes have top-zone rules
    assert "zone_ceiling_exceeded" in {r.rule for r in reg.profiles["cycling.plain.45"].rules}
    assert "top_zone_share_high" in {r.rule for r in reg.profiles["cycling.interval.45"].rules}


def test_the_55_minute_cycling_profiles_are_provisional_info_only_load_notes() -> None:
    """#388, ADR 0017 section 7b: Tukey fences of a reported TSS p25 to p75, per minute of the
    timeline (which is up to 5 minutes shorter than the label), for plain and interval alike
    because the source has no class-type split. Only the two load rules; no median is a bound."""
    reg = load_registry()
    for pid in PROVISIONAL:
        p = reg.profiles[pid]
        assert (p.sport, p.label_minutes, p.evidence, p.snapshot) == (
            "cycling", 55, "moderate", "2026-11-05",
        )  # fmt: skip
        assert {(r.basis, r.severity) for r in p.rules} == {("tukey_iqr", "info")}
        assert {r.rule: r.metric for r in p.rules} == {
            "tss_rate_low": "tss_per_minute",
            "tss_rate_high": "tss_per_minute",
        }
        assert not {r.rule for r in p.rules} & {"sprints_few", "sprints_many"}
    plain, interval = (reg.profiles[i] for i in PROVISIONAL)
    assert [r.model_dump() for r in plain.rules] == [r.model_dump() for r in interval.rules]
    low, high = plain.rules
    # TSS 68.2 (p25 66, p75 72), 55 minute cell: fences 57 and 81; timeline 50 to 55 minutes
    assert (low.low, high.high) == (1.036, 1.62)
    assert low.low is not None
    assert high.high is not None
    assert low.low <= 57 / 55
    assert high.high >= 81 / 50


@pytest.mark.parametrize("severity", ["warn"])
def test_tukey_basis_forces_info_and_moderate_evidence(severity: str) -> None:
    d = json.loads((PROFILES / "cycling" / "plain_55.json").read_text())
    d["rules"][0]["severity"] = severity
    with pytest.raises(ProfileError, match="forces severity info"):
        _parse(d)
    d = json.loads((PROFILES / "cycling" / "plain_55.json").read_text())
    d["evidence"] = "strong"
    with pytest.raises(ProfileError, match="provisional"):
        _parse(d)


def test_running_profiles_are_moderate_info_only_ranges() -> None:
    """P9: fewer than 25 running classes per cell, so only observed ranges as notes (ADR 0017
    sections 1, 2 and 7). No TSS rule (the running load model is not calibrated, ADR 0018) and no
    standing rule (running classes are not ridden standing)."""
    reg = load_registry()
    for pid in RUNNING:
        p = reg.profiles[pid]
        assert (p.sport, p.class_type, p.evidence) == ("running", "interval", "moderate")
        assert 15 <= p.n < E.MIN_N_PERCENTILE
        assert p.snapshot == "2026-11-03"
        assert {(r.basis, r.severity) for r in p.rules} == {("range_10", "info")}
        metrics = {r.metric for r in p.rules}
        assert not metrics & {"tss_per_minute", "standing_time_share"}
        assert {E.RATIONALE_SPORTS.get(r.rationale_id, "running") for r in p.rules} == {"running"}


def test_only_reviewed_cells_ship() -> None:
    reg = load_registry()
    cells = {(p.sport, p.class_type, p.label_minutes) for p in reg.profiles.values()}
    assert {c for c in cells if c[0] == "cycling"} == {
        ("cycling", t, n) for t in ("plain", "interval") for n in (30, 45, 55, 60)
    }
    # running: plain, 55-minute and test cells are weak (n under 15) and ship nothing
    assert {c for c in cells if c[0] == "running"} == {
        ("running", "interval", 30),
        ("running", "interval", 45),
    }


def test_digests_are_stable_and_match_the_index() -> None:
    index = json.loads((PROFILES / "index.json").read_text())
    for pid, entry in index["profiles"].items():
        raw = (PROFILES / entry["file"]).read_bytes()
        assert P.digest_of(raw) == entry["digest"], f"{pid}: bump version and refresh the index"
        # a checkout with other line endings does not change the digest
        assert P.digest_of(raw.replace(b"\n", b"\r\n")) == entry["digest"]
    assert index["rules_version"] == max(e["version"] for e in index["profiles"].values())


def test_changed_file_without_version_and_digest_is_refused(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    f = root / "cycling" / "plain_30.json"
    d = json.loads(f.read_text())
    d["rules"][0]["low"] = d["rules"][0]["low"] + 1
    f.write_text(json.dumps(d, indent=2) + "\n")
    with pytest.raises(ProfileError, match="digest differs"):
        load_registry(root)


def test_changed_version_without_index_is_refused(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    f = root / "cycling" / "plain_30.json"
    d = json.loads(f.read_text())
    d["version"] = 2
    f.write_text(json.dumps(d, indent=2) + "\n")
    with pytest.raises(ProfileError):
        load_registry(root)


def test_unlisted_profile_file_is_refused(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    shutil.copy(root / "cycling" / "plain_30.json", root / "cycling" / "plain_75.json")
    with pytest.raises(ProfileError, match="differ"):
        load_registry(root)


def test_schema_json_is_in_sync_and_closed() -> None:
    assert (PROFILES / "schema.json").read_text() == P.dumps_schema()
    schema = json.loads(P.dumps_schema())

    def closed(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is False
            for v in node.values():
                closed(v)
        elif isinstance(node, list):
            for v in node:
                closed(v)

    closed(schema)


def test_unknown_field_is_refused() -> None:
    with pytest.raises(ProfileError):
        _parse(_profile_dict(title="anything"))
    d = _profile_dict()
    d["rules"][0]["note"] = "x"
    with pytest.raises(ProfileError):
        _parse(d)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source",), "somewhere else"),
        (("snapshot",), "last week"),
        (("id",), "cycling.interval.45 and more"),
        (("rules", 0, "rule"), "a free sentence about warming up"),
        (("rules", 0, "metric"), "free text"),
        (("rules", 0, "rationale_id"), "Most classes ride a while first."),
        (("rules", 0, "brief_overrides"), ["any text at all"]),
        (("sport",), "swimming"),
        (("class_type",), "spin party"),
    ],
)
def test_free_strings_are_refused(path: tuple[Any, ...], value: Any) -> None:
    d = _profile_dict()
    node: Any = d
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ProfileError):
        _parse(d)


def test_percentile_basis_needs_25_classes() -> None:
    assert _parse(_profile_dict(n=25)).n == 25
    with pytest.raises(ProfileError, match="n >= 25"):
        _parse(_profile_dict(n=24))


def test_range_basis_forces_info_and_small_n() -> None:
    d = _profile_dict(n=12, evidence="moderate")
    for r in d["rules"]:
        r["basis"] = "range_10"
        r["severity"] = "warn"
    with pytest.raises(ProfileError):
        _parse(d)
    for r in d["rules"]:
        r["severity"] = "info"
    assert _parse(d).n == 12


def test_evidence_limits() -> None:
    with pytest.raises(ProfileError, match="weak"):
        _parse(_profile_dict(evidence="weak"))
    with pytest.raises(ProfileError, match="moderate"):
        _parse(_profile_dict(evidence="moderate"))
    assert _parse(_profile_dict(evidence="weak", rules=[])).rules == []


def test_rule_must_match_its_metric_and_side() -> None:
    d = _profile_dict()
    d["rules"][0]["metric"] = "cooldown_s"
    with pytest.raises(ProfileError):
        _parse(d)
    d = _profile_dict()
    d["rules"][0]["high"] = d["rules"][0]["low"]
    with pytest.raises(ProfileError):
        _parse(d)
    d = _profile_dict()
    d["rules"][0]["basis"] = "p95"
    with pytest.raises(ProfileError):
        _parse(d)


def test_id_must_match_the_fields() -> None:
    with pytest.raises(ProfileError):
        _parse(_profile_dict(label_minutes=60))


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in [k, *_strings(v)]]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def test_profile_files_hold_only_ids_and_numbers() -> None:
    allowed = {
        *E.SPORTS,
        *E.CLASS_TYPES,
        *E.EVIDENCES,
        *E.BASES,
        *E.SEVERITIES,
        *E.METRICS,
        *E.RULE_IDS,
        *E.RATIONALE_IDS,
        *E.BRIEF_OVERRIDES,
        E.SOURCE,
    }
    keys = set(P.Profile.model_fields) | set(P.ProfileRule.model_fields) | {"schema"}
    files = sorted(PROFILES.glob("*/*.json"))
    assert {f.parent.name for f in files} == set(E.SPORTS)
    sports = "|".join(E.SPORTS)
    for f in files:
        d = json.loads(f.read_text())
        for s in _strings(d):
            if s in allowed or s in keys:
                continue
            assert re.fullmatch(rf"(?:{sports})\.[a-z_]+\.\d+|\d{{4}}-\d{{2}}-\d{{2}}", s), (
                f.name,
                s,
            )
        for r in d["rules"]:
            for v in (r["low"], r["high"]):
                assert v is None or isinstance(v, int | float)


def test_enums_and_rationales_are_consistent() -> None:
    assert set(E.RULE_SPECS) == set(E.RULE_IDS)
    assert {s.metric for s in E.RULE_SPECS.values()} <= set(E.METRICS)
    assert set(RATIONALES) == set(E.RATIONALE_IDS)
    for sentence in RATIONALES.values():
        assert sentence.endswith(".")
        assert not re.search(r"\d", sentence)
        assert len(sentence) <= 100
    used = {r.rationale_id for p in load_registry().profiles.values() for r in p.rules}
    assert used <= set(RATIONALES)


@pytest.mark.parametrize(
    ("class_type", "length", "expected"),
    [
        ("interval", 45, "cycling.interval.45"),
        ("plain", 30, "cycling.plain.30"),
        ("plain", 60, "cycling.plain.60"),
        ("interval", 42, "cycling.interval.45"),  # exactly 3 minutes
        ("interval", 48, "cycling.interval.45"),
        ("interval", 41.9, None),
        ("interval", 48.1, None),
        ("plain", 33, "cycling.plain.30"),
        ("plain", 37, None),
        ("plain", 57, "cycling.plain.60"),  # strong 60 (3 away) beats provisional 55 (2 away)
        ("interval", 57, "cycling.interval.60"),
        ("plain", 58, "cycling.plain.60"),
        ("plain", 56, "cycling.plain.55"),  # 60 is 4 away: only the provisional note
        ("interval", 56, "cycling.interval.55"),
        ("plain", 52, "cycling.plain.55"),
        ("plain", 55, "cycling.plain.55"),
        ("interval", 55, "cycling.interval.55"),
        ("plain", 51.9, None),
        ("plain", 63, "cycling.plain.60"),
        ("plain", 64, None),
        ("plain", 75, None),  # 75 ships nothing
        ("plain", 72, None),
        ("plain", 20, None),
        ("plain", 0, None),
        ("plain", -5, None),
        ("plain", float("nan"), None),
        ("plain", float("inf"), None),
        ("hiit", 30, None),
        ("journey", 45, None),
        ("ftp_test", 60, None),
        ("unknown", 45, None),
    ],
)
def test_select_profile_within_three_minutes(
    class_type: str, length: float, expected: str | None
) -> None:
    got = select_profile(class_type, length)
    assert (got.id if got else None) == expected


def test_strong_profile_in_window_beats_nearer_provisional_one() -> None:
    class Fake:
        def __init__(self, provisional: dict[int, bool]) -> None:
            self.p = provisional

        def labels(self, sport: str, class_type: str) -> list[int]:
            return sorted(self.p)

        def get(self, pid: str) -> P.Profile:
            lab = int(pid.rsplit(".", 1)[1])
            basis = "tukey_iqr" if self.p[lab] else "p5"
            rule = types.SimpleNamespace(basis=basis)
            return types.SimpleNamespace(  # type: ignore[return-value]
                id=pid, label_minutes=lab, rules=[rule]
            )

    reg: Any = Fake({55: True, 60: False})
    assert select_profile("plain", 57, registry=reg).id == "cycling.plain.60"  # type: ignore[union-attr]
    assert select_profile("plain", 56, registry=reg).id == "cycling.plain.55"  # type: ignore[union-attr]
    # Two strong profiles: the nearest still wins.
    reg = Fake({55: False, 60: False})
    assert select_profile("plain", 57, registry=reg).id == "cycling.plain.55"  # type: ignore[union-attr]
    # Two provisional ones: the nearest wins too.
    reg = Fake({55: True, 60: True})
    assert select_profile("plain", 57, registry=reg).id == "cycling.plain.55"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("class_type", "length", "expected"),
    [
        ("interval", 30, "running.interval.30"),
        ("interval", 27, "running.interval.30"),
        ("interval", 33, "running.interval.30"),
        ("interval", 45, "running.interval.45"),
        ("interval", 42, "running.interval.45"),
        ("interval", 48, "running.interval.45"),
        ("interval", 26.9, None),
        ("interval", 37, None),
        ("interval", 48.1, None),
        ("interval", 60, None),  # no running 60 file: cycling's is never borrowed
        ("interval", 55, None),
        ("plain", 30, None),  # weak: no file
        ("plain", 45, None),
        ("hiit", 30, None),
        ("ftp_test", 45, None),
        ("interval", 0, None),
        ("interval", float("nan"), None),
    ],
)
def test_select_profile_for_running(class_type: str, length: float, expected: str | None) -> None:
    got = select_profile(class_type, length, sport="running")
    assert (got.id if got else None) == expected


def test_sports_never_share_a_profile() -> None:
    default, cycling = select_profile("interval", 45), select_profile("interval", 45, "cycling")
    assert default is not None
    assert cycling is not None
    assert default.id == cycling.id == "cycling.interval.45"
    assert select_profile("plain", 45, sport="running") is None
    assert select_profile("plain", 45, sport="swimming") is None
    assert select_profile("interval", 45, sport="Running") is None


def test_a_rationale_worded_for_one_sport_is_refused_in_another() -> None:
    d = _profile_dict()
    d["rules"][0]["rationale_id"] = "warmup_ramp_run"
    with pytest.raises(ProfileError, match="not worded for cycling"):
        _parse(d)
    run = json.loads((PROFILES / "running" / "interval_45.json").read_text())
    assert _parse(run).sport == "running"
    run["rules"][0]["rationale_id"] = "warmup_ramp"
    with pytest.raises(ProfileError, match="not worded for running"):
        _parse(run)


def test_one_sport_wording_matches_its_words() -> None:
    """A sentence that says ride, riding or standing is cycling-only; one that says run or
    running is running-only; the rest say neither."""
    for rid, sentence in RATIONALES.items():
        words = set(re.findall(r"[a-z]+", sentence.lower()))
        ride = bool(words & {"ride", "riding", "standing"})
        run = bool(words & {"run", "running"})
        assert not (ride and run), rid
        expected = "cycling" if ride else "running" if run else None
        assert E.RATIONALE_SPORTS.get(rid) == expected, rid


def test_rules_package_imports_neither_agent_nor_database() -> None:
    banned = ("classbeat.agent", "classbeat.db", "sqlalchemy", "psycopg", "classbeat.jobs")
    for f in RULES_PKG.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(banned), (f.name, n)
